"""Compute torch coverage against material voxels for WAAM parts."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import FreeCAD as App  # type: ignore

from ..core.paths import get_output_dir
from ..mesh_export.torch_coverage_to_llm import AGENT_README_RLE
from ..nc import parser as nc_parser
from .voxel_matrix import _grid_spec, _shape_is_inside, _slugify


def _flatten_index(ix: int, iy: int, iz: int, dims: Tuple[int, int, int]) -> int:
    nx, ny, nz = dims
    return (ix * ny + iy) * nz + iz


def _format_part_label(doc: Optional[App.Document], obj: Optional[App.DocumentObject]) -> str:
    parts: List[str] = []
    if doc is not None:
        doc_label = getattr(doc, "Label", None)
        if doc_label:
            parts.append(str(doc_label))
    if obj is not None:
        obj_label = getattr(obj, "Label", None) or getattr(obj, "Name", None)
        if obj_label:
            parts.append(str(obj_label))
    return " / ".join(parts) if parts else "Part"


def _coerce_points(points: Sequence[Sequence[float]]) -> List[Tuple[float, float, float]]:
    return [(float(p[0]), float(p[1]), float(p[2])) for p in points]


def _coerce_segments(segments: Sequence[bool]) -> List[bool]:
    return [bool(val) for val in segments]


def _sample_segment(
    start: Tuple[float, float, float],
    end: Tuple[float, float, float],
    step: float,
) -> Iterable[Tuple[float, float, float]]:
    sx, sy, sz = start
    ex, ey, ez = end
    dx = ex - sx
    dy = ey - sy
    dz = ez - sz
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length <= 1e-9 or step <= 0.0:
        yield start
        return
    steps = max(1, int(math.ceil(length / step)))
    for idx in range(steps + 1):
        t = idx / float(steps)
        yield (sx + dx * t, sy + dy * t, sz + dz * t)


def _voxelize_torch_path(
    points: Sequence[Tuple[float, float, float]],
    deposition_segments: Sequence[bool],
    origin: Tuple[float, float, float],
    dims: Tuple[int, int, int],
    voxel_size_mm: float,
    bead_width_mm: float,
    layer_height_mm: float,
    sample_step_mm: float,
) -> Set[int]:
    if voxel_size_mm <= 0.0:
        raise ValueError("Voxel size must be > 0.")
    if sample_step_mm <= 0.0:
        raise ValueError("Sample step must be > 0.")
    if bead_width_mm < 0.0 or layer_height_mm < 0.0:
        raise ValueError("Bead width and layer height must be >= 0.")

    nx, ny, nz = dims
    ox, oy, oz = origin
    inv = 1.0 / voxel_size_mm
    radius = 0.5 * bead_width_mm
    half_height = 0.5 * layer_height_mm
    radius_sq = radius * radius

    covered: Set[int] = set()

    def mark(ix: int, iy: int, iz: int) -> None:
        if 0 <= ix < nx and 0 <= iy < ny and 0 <= iz < nz:
            covered.add(_flatten_index(ix, iy, iz, dims))

    for idx, is_deposition in enumerate(deposition_segments):
        if not is_deposition:
            continue
        start = points[idx]
        end = points[idx + 1]
        for x, y, z in _sample_segment(start, end, sample_step_mm):
            ix0 = int(math.floor((x - ox) * inv))
            iy0 = int(math.floor((y - oy) * inv))
            iz0 = int(math.floor((z - oz) * inv))
            mark(ix0, iy0, iz0)

            if radius <= 0.0:
                continue

            min_ix = int(math.floor((x - radius - ox) * inv))
            max_ix = int(math.floor((x + radius - ox) * inv))
            min_iy = int(math.floor((y - radius - oy) * inv))
            max_iy = int(math.floor((y + radius - oy) * inv))
            min_iz = int(math.floor((z - half_height - oz) * inv))
            max_iz = int(math.floor((z + half_height - oz) * inv))

            if max_ix < 0 or max_iy < 0 or max_iz < 0:
                continue
            if min_ix >= nx or min_iy >= ny or min_iz >= nz:
                continue

            min_ix = max(min_ix, 0)
            min_iy = max(min_iy, 0)
            min_iz = max(min_iz, 0)
            max_ix = min(max_ix, nx - 1)
            max_iy = min(max_iy, ny - 1)
            max_iz = min(max_iz, nz - 1)

            for ix in range(min_ix, max_ix + 1):
                cx = ox + (ix + 0.5) * voxel_size_mm
                dx = cx - x
                dx2 = dx * dx
                if dx2 > radius_sq:
                    continue
                for iy in range(min_iy, max_iy + 1):
                    cy = oy + (iy + 0.5) * voxel_size_mm
                    dy = cy - y
                    if dx2 + dy * dy > radius_sq:
                        continue
                    for iz in range(min_iz, max_iz + 1):
                        if half_height > 0.0:
                            cz = oz + (iz + 0.5) * voxel_size_mm
                            if abs(cz - z) > half_height:
                                continue
                        mark(ix, iy, iz)

    return covered


def _rle_update(
    value: int,
    last_value: Optional[int],
    run_count: int,
    runs: List[List[int]],
) -> Tuple[Optional[int], int]:
    if last_value is None:
        return value, 1
    if value == last_value:
        return last_value, run_count + 1
    runs.append([int(last_value), int(run_count)])
    return value, 1


def _rle_block_from_runs(
    runs: List[List[int]],
    dims: Tuple[int, int, int],
    order: str,
) -> Dict[str, object]:
    total = int(dims[0] * dims[1] * dims[2])
    if sum(run[1] for run in runs) != total:
        raise RuntimeError("RLE voxel run length does not match grid size.")
    return {
        "encoding": "rle",
        "order": order,
        "dims": [int(dims[0]), int(dims[1]), int(dims[2])],
        "total_voxels": total,
        "runs": runs,
    }


def _default_torch_coverage_filename(metadata: Dict[str, object]) -> str:
    return "part_torch_coverage.rle.json"


def build_torch_coverage_payload(
    doc: Optional[App.Document] = None,
    obj: Optional[App.DocumentObject] = None,
    nc_path: Optional[Path | str] = None,
    voxel_size_mm: float = 2.0,
    bead_width_mm: float = 2.0,
    layer_height_mm: float = 1.6,
    sample_step_mm: float = 1.0,
    *,
    max_voxels: int = 2_000_000,
    inside_tolerance: float = 1e-3,
    collect_missing_points: bool = False,
    collect_covered_points: bool = False,
    collect_extra_points: bool = False,
    max_missing_points: int = 200_000,
    max_covered_points: int = 200_000,
    max_extra_points: int = 200_000,
) -> Tuple[Dict[str, object], Dict[str, List[App.Vector]]]:
    if nc_path is None:
        raise ValueError("NC path must be provided.")
    nc_file = Path(nc_path).expanduser()
    if not nc_file.is_file():
        raise FileNotFoundError(str(nc_file))

    doc = doc or App.ActiveDocument
    if doc is None:
        raise RuntimeError("No active document.")
    if obj is None:
        obj = doc.ActiveObject

    shape, origin, dims, wcs, metadata = _grid_spec(doc, obj, voxel_size_mm, max_voxels)

    points_raw, deposition_segments_raw = nc_parser.parse_nc_file(nc_file, allow_config=True)
    points = _coerce_points(points_raw)
    deposition_segments = _coerce_segments(deposition_segments_raw)
    if len(deposition_segments) != max(len(points) - 1, 0):
        raise ValueError("NC toolpath arrays have inconsistent lengths.")
    if not any(deposition_segments):
        raise ValueError("No deposition moves detected in NC file.")

    torch_covered = _voxelize_torch_path(
        points,
        deposition_segments,
        origin,
        dims,
        voxel_size_mm,
        bead_width_mm,
        layer_height_mm,
        sample_step_mm,
    )

    nx, ny, nz = dims
    ox, oy, oz = origin
    base = App.Vector(wcs.Base)
    rotation = wcs.Rotation

    material_runs: List[List[int]] = []
    torch_runs: List[List[int]] = []
    last_material: Optional[int] = None
    last_torch: Optional[int] = None
    material_run = 0
    torch_run = 0

    material_voxels = 0
    torch_voxels = 0
    covered_voxels = 0
    missing_voxels = 0
    extra_voxels = 0

    missing_points: List[App.Vector] = []
    covered_points: List[App.Vector] = []
    extra_points: List[App.Vector] = []
    total_voxels = int(nx * ny * nz)

    for ix in range(nx):
        x = ox + (ix + 0.5) * voxel_size_mm
        for iy in range(ny):
            y = oy + (iy + 0.5) * voxel_size_mm
            for iz in range(nz):
                z = oz + (iz + 0.5) * voxel_size_mm
                world = rotation.multVec(App.Vector(x, y, z)) + base
                inside = _shape_is_inside(shape, world, inside_tolerance)
                material_value = 1 if inside else 0
                flat = _flatten_index(ix, iy, iz, dims)
                torch_value = 1 if flat in torch_covered else 0

                last_material, material_run = _rle_update(
                    material_value, last_material, material_run, material_runs
                )
                last_torch, torch_run = _rle_update(
                    torch_value, last_torch, torch_run, torch_runs
                )

                if material_value:
                    material_voxels += 1
                if torch_value:
                    torch_voxels += 1
                if material_value and torch_value:
                    covered_voxels += 1
                    if collect_covered_points and len(covered_points) < max_covered_points:
                        covered_points.append(world)
                elif material_value and not torch_value:
                    missing_voxels += 1
                    if collect_missing_points and len(missing_points) < max_missing_points:
                        missing_points.append(world)
                elif torch_value and not material_value:
                    extra_voxels += 1
                    if collect_extra_points and len(extra_points) < max_extra_points:
                        extra_points.append(world)

    if last_material is None or last_torch is None:
        raise RuntimeError("No voxels generated for coverage analysis.")

    material_runs.append([int(last_material), int(material_run)])
    torch_runs.append([int(last_torch), int(torch_run)])

    metadata = dict(metadata)
    metadata.update(
        {
            "voxel_size_mm": float(voxel_size_mm),
            "bead_width_mm": float(bead_width_mm),
            "layer_height_mm": float(layer_height_mm),
            "sample_step_mm": float(sample_step_mm),
            "torch_path": str(nc_file),
            "part_label": _format_part_label(doc, obj),
        }
    )

    coverage_pct = 0.0
    if material_voxels > 0:
        coverage_pct = covered_voxels / float(material_voxels) * 100.0

    overbuild_pct = 0.0
    if torch_voxels > 0:
        overbuild_pct = extra_voxels / float(torch_voxels) * 100.0

    # Calculate NC path bounds for debugging
    nc_xs = [p[0] for p in points]
    nc_ys = [p[1] for p in points]
    nc_zs = [p[2] for p in points]
    nc_bounds = [
        min(nc_xs) if nc_xs else 0.0, max(nc_xs) if nc_xs else 0.0,
        min(nc_ys) if nc_ys else 0.0, max(nc_ys) if nc_ys else 0.0,
        min(nc_zs) if nc_zs else 0.0, max(nc_zs) if nc_zs else 0.0,
    ]

    summary = {
        "material_voxels": int(material_voxels),
        "torch_voxels": int(torch_voxels),
        "covered_voxels": int(covered_voxels),
        "missing_voxels": int(missing_voxels),
        "extra_voxels": int(extra_voxels),
        "coverage_pct": float(coverage_pct),
        "overbuild_pct": float(overbuild_pct),
        "grid_voxels": total_voxels,
        "nc_bounds": nc_bounds,
        "grid_bounds": metadata.get("bounds_wcs", []),
    }

    payload = {
        "metadata": metadata,
        "summary": summary,
        "material_grid": _rle_block_from_runs(material_runs, dims, metadata.get("order", "xyz")),
        "torch_grid": _rle_block_from_runs(torch_runs, dims, metadata.get("order", "xyz")),
        "agent_readme": AGENT_README_RLE,
        # Comprehensive explicit point lists for debugging
        "missing_points": [[p.x, p.y, p.z] for p in missing_points],
        "covered_points": [[p.x, p.y, p.z] for p in covered_points],
        "extra_points": [[p.x, p.y, p.z] for p in extra_points],
    }

    return payload, {
        "missing": missing_points,
        "covered": covered_points,
        "extra": extra_points,
    }


def write_torch_coverage_json(
    payload: Dict[str, object],
    *,
    output_path: Optional[Path | str] = None,
    output_dir: Optional[Path | str] = None,
    filename: Optional[str] = None,
) -> Path:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("Payload metadata missing or invalid.")

    if output_path:
        target = Path(output_path).expanduser().resolve()
    else:
        root = Path(output_dir).expanduser() if output_dir else get_output_dir()
        target_dir = root / "torch_coverage"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / (filename or _default_torch_coverage_filename(metadata))

    target.parent.mkdir(parents=True, exist_ok=True)
    
    # Extract explicit points from payload if present, to save as separate debugging files
    debug_sets = {}
    for key in ("missing_points", "covered_points", "extra_points"):
        if key in payload:
            debug_sets[key] = payload.pop(key)

    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))

    # Write separate debug files
    stem = target.stem.replace(".rle", "").replace(".json", "")
    for key, points in debug_sets.items():
        if not points:
            continue
        debug_path = target.parent / f"{stem}_{key}.json"
        with debug_path.open("w", encoding="utf-8") as handle:
            json.dump(points, handle, indent=2)

    return target


__all__ = [
    "build_torch_coverage_payload",
    "write_torch_coverage_json",
]
