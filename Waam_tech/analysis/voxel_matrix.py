"""Build a 3D voxel matrix (0/1) for the active part, in WCS coordinates."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import FreeCAD as App  # type: ignore

from ..core.paths import get_output_dir
from ..core.wcs_resolver import resolve_wcs
from ..mesh_export.torch_coverage_to_llm import AGENT_README_RLE


def _resolve_doc(doc: Optional[App.Document]) -> App.Document:
    doc = doc or App.ActiveDocument
    if doc is None:
        raise RuntimeError("No active document.")
    return doc


def _resolve_object(doc: App.Document, obj: Optional[App.DocumentObject]):
    if obj is None:
        obj = doc.ActiveObject
    if obj is None or not hasattr(obj, "Shape"):
        raise RuntimeError("No valid part selected.")
    return obj


def _resolve_wcs(doc: App.Document) -> App.Placement:
    doc = _resolve_doc(doc)
    resolution = resolve_wcs(doc)
    return resolution.placement


def _shape_is_inside(shape, point: App.Vector, tol: float) -> bool:
    if not hasattr(shape, "isInside"):
        return False
    try:
        return bool(shape.isInside(point, tol, False))
    except TypeError:
        try:
            return bool(shape.isInside(point, tol))
        except TypeError:
            try:
                return bool(shape.isInside(point))
            except Exception:
                return False


def _shape_bbox_wcs(shape, wcs: App.Placement) -> Tuple[float, float, float, float, float, float]:
    inv = wcs.inverse()
    xs: List[float] = []
    ys: List[float] = []
    zs: List[float] = []
    bb = shape.BoundBox
    corners = [
        App.Vector(bb.XMin, bb.YMin, bb.ZMin),
        App.Vector(bb.XMin, bb.YMin, bb.ZMax),
        App.Vector(bb.XMin, bb.YMax, bb.ZMin),
        App.Vector(bb.XMin, bb.YMax, bb.ZMax),
        App.Vector(bb.XMax, bb.YMin, bb.ZMin),
        App.Vector(bb.XMax, bb.YMin, bb.ZMax),
        App.Vector(bb.XMax, bb.YMax, bb.ZMin),
        App.Vector(bb.XMax, bb.YMax, bb.ZMax),
    ]
    for corner in corners:
        local = inv.multVec(corner)
        xs.append(float(local.x))
        ys.append(float(local.y))
        zs.append(float(local.z))
    if not xs:
        raise RuntimeError("Shape has no vertices; cannot compute bounding box.")
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def _slugify(value: str) -> str:
    safe = "".join(
        ch.lower() if ch.isascii() and (ch.isalnum() or ch in ("-", "_")) else "_"
        for ch in value.strip()
    )
    safe = safe.strip("_")
    return safe or "part"


def _default_voxel_filename(metadata: Dict[str, object]) -> str:
    label = metadata.get("object_label") or metadata.get("object_name") or "part"
    slug = _slugify(str(label))
    return f"{slug}_voxel_matrix.rle.json"


def _build_matrix(
    doc: Optional[App.Document],
    obj: Optional[App.DocumentObject],
    voxel_size_mm: float,
    max_voxels: int,
    inside_tolerance: float,
) -> Tuple[List[List[List[int]]], Dict[str, object]]:
    shape, origin, dims, wcs, metadata = _grid_spec(doc, obj, voxel_size_mm, max_voxels)

    nx, ny, nz = dims
    xmin, ymin, zmin = origin
    grid = [[[0 for _ in range(nz)] for _ in range(ny)] for _ in range(nx)]
    base = App.Vector(wcs.Base)
    rotation = wcs.Rotation

    filled = 0
    for ix in range(nx):
        x = xmin + (ix + 0.5) * voxel_size_mm
        for iy in range(ny):
            y = ymin + (iy + 0.5) * voxel_size_mm
            for iz in range(nz):
                z = zmin + (iz + 0.5) * voxel_size_mm
                world = rotation.multVec(App.Vector(x, y, z)) + base
                if _shape_is_inside(shape, world, inside_tolerance):
                    grid[ix][iy][iz] = 1
                    filled += 1

    metadata["filled_voxels"] = int(filled)
    return grid, metadata


def _grid_spec(
    doc: Optional[App.Document],
    obj: Optional[App.DocumentObject],
    voxel_size_mm: float,
    max_voxels: int,
) -> Tuple[object, Tuple[float, float, float], Tuple[int, int, int], App.Placement, Dict[str, object]]:
    if voxel_size_mm <= 0.0:
        raise ValueError("Voxel size must be > 0.")

    doc = _resolve_doc(doc)
    obj = _resolve_object(doc, obj)
    shape = obj.Shape
    try:
        if shape.isNull():
            raise RuntimeError("Selected object has no valid shape.")
    except Exception:
        pass

    wcs = _resolve_wcs(doc)
    xmin, xmax, ymin, ymax, zmin, zmax = _shape_bbox_wcs(shape, wcs)
    pad = 0.5 * voxel_size_mm
    xmin -= pad
    xmax += pad
    ymin -= pad
    ymax += pad
    zmin -= pad
    zmax += pad

    span_x = max(xmax - xmin, 0.0)
    span_y = max(ymax - ymin, 0.0)
    span_z = max(zmax - zmin, 0.0)
    nx = max(1, int(math.ceil(span_x / voxel_size_mm)))
    ny = max(1, int(math.ceil(span_y / voxel_size_mm)))
    nz = max(1, int(math.ceil(span_z / voxel_size_mm)))

    total_voxels = nx * ny * nz
    if total_voxels > max_voxels:
        raise RuntimeError(
            f"Requested grid has {total_voxels} voxels; increase voxel size or raise max voxels."
        )

    metadata = {
        "voxel_size_mm": float(voxel_size_mm),
        "dims": [int(nx), int(ny), int(nz)],
        "origin_wcs": [float(xmin), float(ymin), float(zmin)],
        "bounds_wcs": [float(xmin), float(xmax), float(ymin), float(ymax), float(zmin), float(zmax)],
        "order": "xyz",
        "object_name": getattr(obj, "Name", ""),
        "object_label": getattr(obj, "Label", ""),
    }
    return shape, (xmin, ymin, zmin), (nx, ny, nz), wcs, metadata


def build_voxel_points(
    doc: Optional[App.Document] = None,
    obj: Optional[App.DocumentObject] = None,
    voxel_size_mm: float = 2.0,
    *,
    max_voxels: int = 2_000_000,
    inside_tolerance: float = 1e-3,
) -> Tuple[List[App.Vector], Dict[str, object]]:
    """Return a list of world-space points for voxels that contain material."""
    shape, origin, dims, wcs, metadata = _grid_spec(doc, obj, voxel_size_mm, max_voxels)

    nx, ny, nz = dims
    xmin, ymin, zmin = origin
    base = App.Vector(wcs.Base)
    rotation = wcs.Rotation

    points: List[App.Vector] = []
    for ix in range(nx):
        x = xmin + (ix + 0.5) * voxel_size_mm
        for iy in range(ny):
            y = ymin + (iy + 0.5) * voxel_size_mm
            for iz in range(nz):
                z = zmin + (iz + 0.5) * voxel_size_mm
                world = rotation.multVec(App.Vector(x, y, z)) + base
                if _shape_is_inside(shape, world, inside_tolerance):
                    points.append(world)

    metadata["filled_voxels"] = int(len(points))
    return points, metadata


def build_voxel_points_and_rle_payload(
    doc: Optional[App.Document] = None,
    obj: Optional[App.DocumentObject] = None,
    voxel_size_mm: float = 2.0,
    *,
    max_voxels: int = 2_000_000,
    inside_tolerance: float = 1e-3,
) -> Tuple[List[App.Vector], Dict[str, object], Dict[str, object]]:
    """Return points + an RLE payload for the material voxel grid."""
    shape, origin, dims, wcs, metadata = _grid_spec(doc, obj, voxel_size_mm, max_voxels)

    nx, ny, nz = dims
    xmin, ymin, zmin = origin
    base = App.Vector(wcs.Base)
    rotation = wcs.Rotation

    points: List[App.Vector] = []
    runs: List[List[int]] = []
    last_value: Optional[int] = None
    run_count = 0
    filled = 0

    for ix in range(nx):
        x = xmin + (ix + 0.5) * voxel_size_mm
        for iy in range(ny):
            y = ymin + (iy + 0.5) * voxel_size_mm
            for iz in range(nz):
                z = zmin + (iz + 0.5) * voxel_size_mm
                world = rotation.multVec(App.Vector(x, y, z)) + base
                inside = _shape_is_inside(shape, world, inside_tolerance)
                value = 1 if inside else 0

                if inside:
                    points.append(world)
                    filled += 1

                if last_value is None:
                    last_value = value
                    run_count = 1
                elif value == last_value:
                    run_count += 1
                else:
                    runs.append([last_value, run_count])
                    last_value = value
                    run_count = 1

    if last_value is None:
        raise RuntimeError("No voxel samples generated.")
    runs.append([last_value, run_count])

    total_voxels = int(nx * ny * nz)
    if sum(run[1] for run in runs) != total_voxels:
        raise RuntimeError("RLE voxel run length does not match grid size.")

    metadata["filled_voxels"] = int(filled)
    summary = {
        "material_voxels": int(filled),
        "grid_voxels": total_voxels,
    }
    payload = {
        "metadata": metadata,
        "summary": summary,
        "material_grid": {
            "encoding": "rle",
            "order": metadata.get("order", "xyz"),
            "dims": [int(nx), int(ny), int(nz)],
            "total_voxels": total_voxels,
            "runs": runs,
        },
        "agent_readme": AGENT_README_RLE,
    }

    return points, metadata, payload


def create_voxel_point_cloud(
    doc: Optional[App.Document] = None,
    obj: Optional[App.DocumentObject] = None,
    voxel_size_mm: float = 2.0,
    *,
    max_voxels: int = 2_000_000,
    inside_tolerance: float = 1e-3,
    name: str = "WAAM_VoxelPoints",
    point_size: int = 2,
    point_color: Tuple[float, float, float] = (0.95, 0.35, 0.1),
    points: Optional[List[App.Vector]] = None,
    metadata: Optional[Dict[str, object]] = None,
) -> Tuple[App.DocumentObject, Dict[str, object]]:
    """Create or update a Points::Feature for voxel centers with material."""
    doc = _resolve_doc(doc)
    if points is None or metadata is None:
        points, metadata = build_voxel_points(
            doc=doc,
            obj=obj,
            voxel_size_mm=voxel_size_mm,
            max_voxels=max_voxels,
            inside_tolerance=inside_tolerance,
        )

    if not points:
        raise RuntimeError("No material voxels found at this resolution.")

    try:
        import Points  # type: ignore
    except Exception as exc:  # pragma: no cover - FreeCAD dependency
        raise RuntimeError(f"FreeCAD Points module unavailable: {exc}") from exc

    point_cloud = Points.Points(points)
    existing = doc.getObject(name)
    if existing and getattr(existing, "TypeId", "") == "Points::Feature":
        target = existing
    elif existing:
        raise RuntimeError(f"Object '{name}' exists and is not a Points::Feature.")
    else:
        target = doc.addObject("Points::Feature", name)

    target.Points = point_cloud
    try:
        target.Label = "WAAM Voxel Points"
    except Exception:
        pass
    try:
        target.ViewObject.PointSize = int(point_size)
    except Exception:
        pass
    try:
        target.ViewObject.PointColor = point_color
    except Exception:
        pass

    doc.recompute()
    return target, metadata


def write_voxel_rle_json(
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
        target_dir = root / "voxel_matrix"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / (filename or _default_voxel_filename(metadata))

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
    return target


def build_voxel_matrix(
    doc: Optional[App.Document] = None,
    obj: Optional[App.DocumentObject] = None,
    voxel_size_mm: float = 2.0,
    *,
    max_voxels: int = 2_000_000,
    inside_tolerance: float = 1e-3,
) -> List[List[List[int]]]:
    """Return grid[x][y][z] with 1 for material and 0 for empty space."""
    matrix, _ = _build_matrix(doc, obj, voxel_size_mm, max_voxels, inside_tolerance)
    return matrix


def build_voxel_payload(
    doc: Optional[App.Document] = None,
    obj: Optional[App.DocumentObject] = None,
    voxel_size_mm: float = 2.0,
    *,
    max_voxels: int = 2_000_000,
    inside_tolerance: float = 1e-3,
) -> Dict[str, object]:
    """Return {"matrix": grid, "metadata": {...}} for the active part."""
    matrix, metadata = _build_matrix(doc, obj, voxel_size_mm, max_voxels, inside_tolerance)
    return {"matrix": matrix, "metadata": metadata}


__all__ = [
    "build_voxel_matrix",
    "build_voxel_payload",
    "build_voxel_points",
    "build_voxel_points_and_rle_payload",
    "create_voxel_point_cloud",
    "write_voxel_rle_json",
]
