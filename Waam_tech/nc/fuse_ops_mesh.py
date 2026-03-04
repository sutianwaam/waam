"""Helpers to build a single LLM-ready JSON with ops, config, features, WAAM DSL, and mesh context."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Mapping, MutableMapping, Optional, Sequence

from ..core.config_loader import resolve_standard_config_path
from ..core.paths import (
    _output_root_for,
    get_features_path,
    get_operations_with_mesh_path,
)
from ..llm_agent.api import SpecLoadError, summarize_mesh_context, summarize_spec
from ..operations.spiral_stc import spiral_coverage_from_polygon


class FusionError(RuntimeError):
    """Raised when WAAM operations + mesh fusion cannot complete."""


def _read_json_file(path: Path, label: str) -> tuple[MutableMapping, Mapping[str, object]]:
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise FusionError(f"{label} not found: {path}") from exc
    except OSError as exc:
        raise FusionError(f"Unable to read {label}: {exc}") from exc

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("utf-8", errors="ignore")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FusionError(f"Invalid JSON in {label}: {exc}") from exc
    if not isinstance(data, MutableMapping):
        raise FusionError(f"{label} must contain an object at the top level.")

    meta = {
        "path": str(path),
        "size_bytes": len(raw_bytes),
        "sha1": hashlib.sha1(raw_bytes).hexdigest(),
    }
    return data, meta


def _read_text_payload(path: Path, label: str, *, max_chars: int = 500_000) -> Mapping[str, object]:
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise FusionError(f"{label} not found: {path}") from exc
    except OSError as exc:
        raise FusionError(f"Unable to read {label}: {exc}") from exc

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("utf-8", errors="ignore")

    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    return {
        "path": str(path),
        "size_bytes": len(raw_bytes),
        "sha1": hashlib.sha1(raw_bytes).hexdigest(),
        "text": text,
        "truncated": truncated,
    }


def _default_llm_waam_path(root: Path) -> Path:
    return root / "dsl_waam" / "part.llm_waam"


def _read_mesh_raw(path: Path, *, max_chars: int = 1_200_000) -> Mapping[str, object]:
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise FusionError(f"Mesh file not found: {path}") from exc
    except OSError as exc:
        raise FusionError(f"Unable to read mesh file: {exc}") from exc

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("utf-8", errors="ignore")

    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    return {
        "path": str(path),
        "size_bytes": len(raw_bytes),
        "sha1": hashlib.sha1(raw_bytes).hexdigest(),
        "text": text,
        "truncated": truncated,
    }


def _as_vec3(candidate: object) -> Optional[List[float]]:
    if not isinstance(candidate, Sequence) or len(candidate) < 3:
        return None
    try:
        return [float(candidate[0]), float(candidate[1]), float(candidate[2])]
    except Exception:
        return None


def _normalize_vec(vec: List[float]) -> Optional[List[float]]:
    length = math.sqrt(vec[0] ** 2 + vec[1] ** 2 + vec[2] ** 2)
    if length <= 0.0:
        return None
    return [vec[0] / length, vec[1] / length, vec[2] / length]


def _matrix_from_axes(origin: object, axes: object) -> Optional[List[List[float]]]:
    if not isinstance(axes, Mapping):
        return None
    origin_vec = _as_vec3(origin)
    x_axis = _as_vec3(axes.get("x"))
    y_axis = _as_vec3(axes.get("y"))
    z_axis = _as_vec3(axes.get("z"))
    if origin_vec is None or x_axis is None or y_axis is None or z_axis is None:
        return None
    x_axis = _normalize_vec(x_axis)
    y_axis = _normalize_vec(y_axis)
    z_axis = _normalize_vec(z_axis)
    if x_axis is None or y_axis is None or z_axis is None:
        return None
    return [
        [x_axis[0], y_axis[0], z_axis[0], origin_vec[0]],
        [x_axis[1], y_axis[1], z_axis[1], origin_vec[1]],
        [x_axis[2], y_axis[2], z_axis[2], origin_vec[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _invert_rigid(matrix: List[List[float]]) -> Optional[List[List[float]]]:
    if len(matrix) < 3:
        return None
    try:
        r00, r01, r02, tx = matrix[0]
        r10, r11, r12, ty = matrix[1]
        r20, r21, r22, tz = matrix[2]
    except Exception:
        return None
    # Rotation is orthonormal, so inverse is transpose.
    t0 = -(r00 * tx + r10 * ty + r20 * tz)
    t1 = -(r01 * tx + r11 * ty + r21 * tz)
    t2 = -(r02 * tx + r12 * ty + r22 * tz)
    return [
        [r00, r10, r20, t0],
        [r01, r11, r21, t1],
        [r02, r12, r22, t2],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _compute_frames(features: Mapping[str, object], mesh_summary: Mapping[str, object]) -> Dict[str, object]:
    """Infer a simple CAD->mesh translation when bboxes differ."""
    bbox_feat = (features.get("metadata") or {}).get("bbox") or {}
    bbox_mesh_min = mesh_summary.get("bbox_min")
    bbox_mesh_max = mesh_summary.get("bbox_max")
    frames = {
        "mesh_frame": {"units": "mm"},
        "cad_frame": {"units": (features.get("metadata") or {}).get("units", "mm")},
    }
    wcs = (features.get("metadata") or {}).get("wcs") or (mesh_summary.get("wcs") if isinstance(mesh_summary.get("wcs"), dict) else None)
    if wcs:
        frames["wcs"] = wcs
        if isinstance(wcs, Mapping):
            placement = wcs.get("placement")
            if isinstance(placement, Mapping):
                origin = placement.get("origin_world") or placement.get("origin")
                axes = placement.get("axes_world") or placement.get("axes")
                wcs_to_mesh = _matrix_from_axes(origin, axes)
                if wcs_to_mesh:
                    frames["T_wcs_to_mesh"] = wcs_to_mesh
                    mesh_to_wcs = _invert_rigid(wcs_to_mesh)
                    if mesh_to_wcs:
                        frames["T_mesh_to_wcs"] = mesh_to_wcs

    try:
        cad_min = [float(bbox_feat.get("xmin", 0.0)), float(bbox_feat.get("ymin", 0.0)), float(bbox_feat.get("zmin", 0.0))]
        mesh_min = [float(bbox_mesh_min[0]), float(bbox_mesh_min[1]), float(bbox_mesh_min[2])] if bbox_mesh_min else None
    except Exception:
        cad_min = mesh_min = None

    if mesh_min and cad_min:
        tx = mesh_min[0] - cad_min[0]
        ty = mesh_min[1] - cad_min[1]
        tz = mesh_min[2] - cad_min[2]
        frames["T_cad_to_mesh"] = [
            [1.0, 0.0, 0.0, tx],
            [0.0, 1.0, 0.0, ty],
            [0.0, 0.0, 1.0, tz],
            [0.0, 0.0, 0.0, 1.0],
        ]
    return frames


def _parse_llm_paths(text: str) -> Dict[str, object]:
    """Extract structured paths/layers from compact .llm_waam text."""
    paths: Dict[str, object] = {}
    layers = []
    current_id: Optional[str] = None
    current_role: Optional[str] = None
    points: list[list[float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("PATH"):
            current_id = None
            current_role = None
            points = []
            match_id = re.search(r"ID=([^\s]+)", line)
            if match_id:
                current_id = match_id.group(1)
            match_role = re.search(r"ROLE_HINT=([^\s]+)", line)
            if match_role:
                current_role = match_role.group(1)
            continue
        if line.startswith("END_PATH"):
            if current_id and points:
                closed = len(points) > 2 and points[0] == points[-1]
                paths[current_id] = {
                    "id": current_id,
                    "role_hint": current_role,
                    "frame": "mesh_frame",
                    "closed": closed,
                    "points_xyz": [[p[0], p[1], 0.0] for p in points],
                }
            current_id = None
            current_role = None
            points = []
            continue
        if line.startswith("LAYER"):
            parts = line.split()
            index = None
            layer_z = None
            try:
                index = int(parts[1])
            except Exception:
                index = None
            for token in parts:
                if token.startswith("Z="):
                    try:
                        layer_z = float(token.split("=", 1)[1])
                    except Exception:
                        layer_z = None
            layers.append({"i": index, "z": layer_z, "path_ids": [], "frame": "mesh_frame"})
            continue
        if line.startswith("USE_PATH"):
            match_path = re.search(r"ID=([^\s]+)", line)
            pid = match_path.group(1) if match_path else None
            if pid and layers:
                layers[-1]["path_ids"].append(pid)
            continue
        if line.startswith("PATHLIB") or line.startswith("END_PART") or line.startswith("UNITS") or line.startswith("WAAMPART"):
            continue
        # Point rows
        coords = re.findall(r"\((-?\d+\.?\d*),\s*(-?\d+\.?\d*)\)", line)
        for x_str, y_str in coords:
            try:
                points.append([float(x_str), float(y_str)])
            except Exception:
                continue
    return {"paths": paths, "layers": layers}


def _inject_base_fill(llm_structured: Dict[str, object], *, spacing: float = 6.0, z0: float = 0.0, z1: float = 1.2) -> Dict[str, object]:
    """Add a synthetic base layer with zig-zag infill without modifying the source DSL."""
    paths = dict(llm_structured.get("paths") or {})
    layers = list(llm_structured.get("layers") or [])
    if not paths or "P_BASE_FILL" in paths:
        return llm_structured

    # Skip if an existing layer already starts at or below the requested base height
    min_z = None
    for lyr in layers:
        try:
            z_val = float(lyr.get("z"))
            min_z = z_val if min_z is None else min(min_z, z_val)
        except Exception:
            continue
    if min_z is not None and min_z <= z1 + 1e-3:
        return llm_structured

    # Prefer P2 as the outer contour; otherwise fallback to the first OUTER role
    outer_id = "P2" if "P2" in paths else next(
        (pid for pid, pdata in paths.items() if str(pdata.get("role_hint") or "").upper() == "OUTER"), None
    )
    outer_pts = (paths.get(outer_id) or {}).get("points_xyz") if outer_id else None
    if not outer_id or not outer_pts:
        return llm_structured

    xs = [pt[0] for pt in outer_pts if len(pt) >= 2]
    ys = [pt[1] for pt in outer_pts if len(pt) >= 2]
    if not xs or not ys:
        return llm_structured
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    inset = max(0.0, min(spacing * 0.5, (x_max - x_min) * 0.05, (y_max - y_min) * 0.05))
    x0, x1 = x_min + inset, x_max - inset
    y0, y1 = y_min + inset, y_max - inset
    if x1 <= x0 or y1 <= y0:
        return llm_structured

    pts_fill: list[list[float]] = []
    y = y0 + spacing * 0.5
    forward = True
    while y <= y1:
        if forward:
            pts_fill.extend([[x0, y, 0.0], [x1, y, 0.0]])
        else:
            pts_fill.extend([[x1, y, 0.0], [x0, y, 0.0]])
        forward = not forward
        y += spacing

    frame = (paths.get(outer_id) or {}).get("frame", "mesh_frame")
    paths["P_BASE_FILL"] = {
        "id": "P_BASE_FILL",
        "role_hint": "INFILL",
        "frame": frame,
        "closed": False,
        "points_xyz": pts_fill,
    }

    base0 = {"i": 0, "z": z0, "path_ids": [outer_id, "P_BASE_FILL"], "frame": frame}
    base1 = {"i": 1, "z": z1, "path_ids": [outer_id, "P_BASE_FILL"], "frame": frame}

    llm_structured["paths"] = paths
    llm_structured["layers"] = [base0, base1] + layers
    return llm_structured


def _rotate_point(
    x: float,
    y: float,
    *,
    angle_rad: float,
    origin: tuple[float, float],
    inverse: bool = False,
) -> tuple[float, float]:
    if inverse:
        angle_rad = -angle_rad
    ox, oy = origin
    dx, dy = x - ox, y - oy
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return ox + (dx * cos_a - dy * sin_a), oy + (dx * sin_a + dy * cos_a)


def _ensure_closed_loop_2d(points: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
    pts = [(float(p[0]), float(p[1])) for p in points if len(p) >= 2]
    if len(pts) < 3:
        return []
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _hatch_segments_from_polygon(
    polygon: Sequence[Sequence[float]],
    *,
    spacing: float,
    angle_deg: float,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    pts = _ensure_closed_loop_2d(polygon)
    if not pts or spacing <= 0:
        return []

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    angle_rad = math.radians(angle_deg % 180.0)

    rotated = [_rotate_point(x, y, angle_rad=angle_rad, origin=(cx, cy)) for x, y in pts]
    y_vals = [p[1] for p in rotated]
    y_min, y_max = min(y_vals), max(y_vals)
    y = y_min + spacing * 0.5

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    while y <= y_max + 1e-6:
        x_intersections: list[float] = []
        for (x1, y1), (x2, y2) in zip(rotated, rotated[1:]):
            if abs(y1 - y2) < 1e-9:
                continue
            if (y1 <= y < y2) or (y2 <= y < y1):
                t = (y - y1) / (y2 - y1)
                x_intersections.append(x1 + t * (x2 - x1))
        x_intersections.sort()
        for i in range(0, len(x_intersections) - 1, 2):
            x0 = x_intersections[i]
            x1 = x_intersections[i + 1]
            if abs(x1 - x0) < 1e-6:
                continue
            start = _rotate_point(x0, y, angle_rad=angle_rad, origin=(cx, cy), inverse=True)
            end = _rotate_point(x1, y, angle_rad=angle_rad, origin=(cx, cy), inverse=True)
            segments.append((start, end))
        y += spacing

    return segments


def _inject_hatch_infill(
    llm_structured: Dict[str, object],
    *,
    config: Mapping[str, object],
    max_segments_per_layer: int = 800,
) -> Dict[str, object]:
    paths = dict(llm_structured.get("paths") or {})
    layers = list(llm_structured.get("layers") or [])
    if not paths or not layers:
        return llm_structured

    if any(
        str(p.get("role_hint") or "").upper() == "INFILL"
        and str(p.get("id") or "") not in {"P_BASE_FILL"}
        for p in paths.values()
    ):
        return llm_structured

    dep = (config.get("process") or {}).get("deposition") or {}
    bead_width = float(dep.get("bead_width_mm") or dep.get("bead_width") or 6.0)
    overlap_pct = float(dep.get("overlap_pct") or 30.0)
    spacing = bead_width * max(0.1, 1.0 - overlap_pct / 100.0)
    spacing = max(spacing, 0.5)

    used_ids = set(paths.keys())
    infill_counter = 0

    for idx, layer in enumerate(layers):
        layer_path_ids = list(layer.get("path_ids") or [])
        if not layer_path_ids:
            continue
        if any("INFILL" in str(pid).upper() for pid in layer_path_ids):
            continue

        try:
            layer_index = int(layer.get("i", idx))
        except Exception:
            layer_index = idx
        angle = 90.0 if (layer_index % 2) else 0.0

        new_ids: list[str] = []
        seg_count = 0
        for pid in layer_path_ids:
            pdata = paths.get(pid) or {}
            role = str(pdata.get("role_hint") or "").upper()
            if role in {"INFILL", "INNER", "HOLE"}:
                continue
            pts = pdata.get("points_xyz") or pdata.get("points") or []
            segments = _hatch_segments_from_polygon(pts, spacing=spacing, angle_deg=angle)
            for (sx, sy), (ex, ey) in segments:
                if seg_count >= max_segments_per_layer:
                    break
                infill_counter += 1
                new_id = f"{pid}_INFILL_{layer_index}_{infill_counter}"
                while new_id in used_ids:
                    infill_counter += 1
                    new_id = f"{pid}_INFILL_{layer_index}_{infill_counter}"
                used_ids.add(new_id)
                paths[new_id] = {
                    "id": new_id,
                    "role_hint": "INFILL",
                    "frame": pdata.get("frame", "mesh_frame"),
                    "closed": False,
                    "points_xyz": [[sx, sy, 0.0], [ex, ey, 0.0]],
                }
                new_ids.append(new_id)
                seg_count += 1
            if seg_count >= max_segments_per_layer:
                break

        if new_ids:
            layer["path_ids"] = layer_path_ids + new_ids

    llm_structured["paths"] = paths
    llm_structured["layers"] = layers
    return llm_structured


def _infill_strategy(config: Mapping[str, object]) -> str:
    path_cfg = (config.get("process") or {}).get("path") or {}
    strategy = path_cfg.get("infill_strategy") or "hatch"
    return str(strategy).strip().lower()


def _inject_spiral_infill(
    llm_structured: Dict[str, object],
    *,
    config: Mapping[str, object],
) -> Dict[str, object]:
    paths = dict(llm_structured.get("paths") or {})
    layers = list(llm_structured.get("layers") or [])
    if not paths or not layers:
        return llm_structured

    if any(
        str(p.get("role_hint") or "").upper() == "INFILL"
        and str(p.get("id") or "") not in {"P_BASE_FILL"}
        for p in paths.values()
    ):
        return llm_structured

    dep = (config.get("process") or {}).get("deposition") or {}
    path_cfg = (config.get("process") or {}).get("path") or {}
    bead_width = float(dep.get("bead_width_mm") or dep.get("bead_width") or 6.0)
    overlap_pct = float(dep.get("overlap_pct") or 30.0)
    spacing = bead_width * max(0.1, 1.0 - overlap_pct / 100.0)
    spacing = max(spacing, 0.5)
    max_nodes = int(path_cfg.get("spiral_max_nodes") or 20000)

    used_ids = set(paths.keys())
    infill_counter = 0

    for idx, layer in enumerate(layers):
        layer_path_ids = list(layer.get("path_ids") or [])
        if not layer_path_ids:
            continue
        if any("INFILL" in str(pid).upper() for pid in layer_path_ids):
            continue

        try:
            layer_index = int(layer.get("i", idx))
        except Exception:
            layer_index = idx

        new_ids: list[str] = []
        for pid in layer_path_ids:
            pdata = paths.get(pid) or {}
            role = str(pdata.get("role_hint") or "").upper()
            if role in {"INFILL", "INNER", "HOLE"}:
                continue
            pts = pdata.get("points_xyz") or pdata.get("points") or []
            spiral_pts = spiral_coverage_from_polygon(pts, spacing=spacing, max_merged_nodes=max_nodes)
            if not spiral_pts:
                continue
            infill_counter += 1
            new_id = f"{pid}_SPIRAL_{layer_index}_{infill_counter}"
            while new_id in used_ids:
                infill_counter += 1
                new_id = f"{pid}_SPIRAL_{layer_index}_{infill_counter}"
            used_ids.add(new_id)
            paths[new_id] = {
                "id": new_id,
                "role_hint": "INFILL",
                "frame": pdata.get("frame", "mesh_frame"),
                "closed": False,
                "points_xyz": [[x, y, 0.0] for x, y in spiral_pts],
            }
            new_ids.append(new_id)

        if new_ids:
            layer["path_ids"] = layer_path_ids + new_ids

    llm_structured["paths"] = paths
    llm_structured["layers"] = layers
    return llm_structured


def _validate_units(config: Mapping[str, object], features: Mapping[str, object], ops: Mapping[str, object]) -> Sequence[str]:
    warnings: list[str] = []
    cfg_units = config.get("units")
    feat_units = (features.get("metadata") or {}).get("units")
    ops_units = (ops.get("metadata") or {}).get("units")
    unique = {u for u in (cfg_units, feat_units, ops_units) if u}
    if len(unique) > 1:
        warnings.append(f"Unit mismatch detected (config={cfg_units}, features={feat_units}, operations={ops_units}).")
    return warnings


def _summarize_post_contract(post: Mapping[str, object]) -> Dict[str, object]:
    if not isinstance(post, Mapping):
        return {}
    keys = ("dialect", "torch_on", "torch_off", "gas_on", "gas_off", "wire_feed", "travel_speed", "line_numbers")
    return {key: post.get(key) for key in keys if key in post}


def _summarize_constraints(config: Mapping[str, object]) -> Dict[str, object]:
    proc = config.get("process") or {}
    interpass = proc.get("interpass") or {}
    material = config.get("material") or {}
    quality = config.get("quality") or {}
    safety = config.get("safety") or {}
    return {
        "interpass": {k: interpass.get(k) for k in ("max_temp_c", "max_dwell_s", "cooling", "adaptive_dwell")},
        "material": {k: material.get(k) for k in ("substrate", "wire", "interpass_temp_max_c")},
        "quality": quality,
        "safety": safety,
    }


def _merge_override(base: MutableMapping[str, object], override: Mapping[str, object]) -> MutableMapping[str, object]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), Mapping):
            base[key] = _merge_override(dict(base.get(key) or {}), value)
        else:
            base[key] = value
    return base


def _pathlib_stats(text: str) -> Dict[str, object]:
    path_ids = []
    role_hints = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("PATH"):
            continue
        match = re.search(r"ID=([^\s]+)", line)
        if match:
            path_ids.append(match.group(1))
        role = None
        role_match = re.search(r"ROLE_HINT=([^\s]+)", line)
        if role_match:
            role = role_match.group(1)
        if role:
            role_hints.append(role)
    layer_count = len([ln for ln in text.splitlines() if ln.strip().startswith("LAYER")])
    return {
        "path_count": len(path_ids),
        "unique_paths": sorted(set(path_ids)),
        "layer_count": layer_count,
        "role_hints": sorted(set(role_hints)),
    }


def fuse_operations_with_mesh(
    *,
    ops_path: Path | str,
    mesh_path: Path | str,
    output_path: Optional[Path | str] = None,
    config_path: Optional[Path | str] = None,
    config_override: Optional[Mapping[str, object]] = None,
    features_path: Optional[Path | str] = None,
    llm_waam_path: Optional[Path | str] = None,
) -> Path:
    """
    Build a single JSON that contains:
    - Deterministic WAAM operations (sequence, tools, post settings).
    - The standard WAAM config (machine + process constraints).
    - Extracted feature metadata.
    - Compact WAAM DSL (.llm_waam) text for the part.
    - Mesh context summary (bbox/vertices/faces and mesh text when available).
    """

    ops_file = Path(ops_path).expanduser().resolve()
    mesh_file = Path(mesh_path).expanduser().resolve()

    root = _output_root_for(ops_file)
    cfg_path = Path(config_path).expanduser().resolve() if config_path else resolve_standard_config_path()
    feats_path = (
        Path(features_path).expanduser().resolve()
        if features_path
        else get_features_path(root, prefer_existing=True, create=False)
    )
    llm_path = Path(llm_waam_path).expanduser().resolve() if llm_waam_path else _default_llm_waam_path(root)

    ops, ops_meta = _read_json_file(ops_file, "WAAM operations")
    config, cfg_meta = _read_json_file(cfg_path, "WAAM config")
    if config_override:
        config = _merge_override(dict(config), config_override)
        cfg_meta = dict(cfg_meta)
        cfg_meta["override"] = config_override
    features, feats_meta = _read_json_file(feats_path, "features.json")

    try:
        mesh_summary: Mapping[str, object] = summarize_mesh_context(mesh_file)
    except SpecLoadError as exc:
        raise FusionError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise FusionError(f"Failed to read mesh context: {exc}") from exc

    mesh_summary = dict(mesh_summary)
    mesh_summary["sha1"] = hashlib.sha1(mesh_file.read_bytes()).hexdigest()
    mesh_raw = _read_mesh_raw(mesh_file, max_chars=1_200_000)

    llm_payload = _read_text_payload(llm_path, "LLM WAAM DSL", max_chars=350_000)
    llm_stats = _pathlib_stats(llm_payload.get("text", ""))
    llm_structured = _parse_llm_paths(llm_payload.get("text", ""))
    llm_structured = _inject_base_fill(llm_structured)
    strategy = _infill_strategy(config)
    if strategy in {"spiral_stc", "spiral", "spiral_spanning_tree"}:
        llm_structured = _inject_spiral_infill(llm_structured, config=config)
    else:
        llm_structured = _inject_hatch_infill(llm_structured, config=config)

    spec_overview = summarize_spec(features, ops)
    mesh_overview = {
        "bbox_min": mesh_summary.get("bbox_min"),
        "bbox_max": mesh_summary.get("bbox_max"),
        "vertices": mesh_summary.get("vertices"),
        "faces": mesh_summary.get("faces"),
        "format": mesh_summary.get("format"),
    }
    unit_warnings = _validate_units(config, features, ops)
    post_contract = _summarize_post_contract(config.get("post") or ops.get("post") or {})
    constraints = _summarize_constraints(config)
    frames = _compute_frames(features, mesh_summary)
    waam_strategy = {
        "lead_in_mm": (config.get("process") or {}).get("path", {}).get("lead_in_radius_mm"),
        "lead_out_mm": (config.get("process") or {}).get("path", {}).get("lead_out_radius_mm"),
        "corner_blend_mm": (config.get("process") or {}).get("path", {}).get("min_corner_radius_mm"),
        "alternate_layer_direction": True,
        "interpass": {
            "target_c": (config.get("process") or {}).get("interpass", {}).get("target_temp_c"),
            "max_c": (config.get("process") or {}).get("interpass", {}).get("max_temp_c"),
            "min_dwell_s": (config.get("process") or {}).get("interpass", {}).get("min_temp_c"),
            "max_dwell_s": (config.get("process") or {}).get("interpass", {}).get("max_dwell_s"),
        },
        "bead_model": {
            "nominal_bead_width_mm": (config.get("process") or {}).get("deposition", {}).get("bead_width_mm"),
            "nominal_layer_height_mm": (config.get("process") or {}).get("deposition", {}).get("layer_height_mm"),
            "nominal_travel_mm_min": (config.get("process") or {}).get("deposition", {}).get("travel_speed_mm_min"),
            "nominal_wire_feed_mm_min": (config.get("process") or {}).get("deposition", {}).get("wire_feed_mm_min"),
        },
    }
    validation_rules = {
        "stay_within_travel_mm": (config.get("machine") or {}).get("travel"),
        "min_clearance_z": (ops.get("metadata") or {}).get("clearance"),
        "max_feed_xy": (config.get("machine") or {}).get("max_feed_xy"),
        "no_arc_during_rapid": True,
    }
    ops_sources = ops.get("sources") or {}
    ops_sources.update(
        {
            "operations_path": str(ops_file),
            "mesh_context_path": str(mesh_file),
            "config_path": str(cfg_path),
            "features_path": str(feats_path),
            "llm_waam_path": str(llm_path),
            "operations_sha1": ops_meta.get("sha1"),
        }
    )

    timeline = [
        {"type": "set_wcs", "wcs": (features.get("metadata") or {}).get("wcs", {}).get("name", "G54")},
        {"type": "set_units", "units": config.get("units", "mm")},
        {"type": "set_clearance", "z": (ops.get("metadata") or {}).get("clearance", 0)},
        {"type": "per_layer_paths", "frame": "mesh_frame", "layers": llm_structured.get("layers")},
    ]
    toolpath = {
        "frames": frames,
        "paths": llm_structured.get("paths"),
        "layers": llm_structured.get("layers"),
        "timeline": timeline,
        "waam_strategy": waam_strategy,
        "validation_rules": validation_rules,
        "post_contract": post_contract,
        "constraints": constraints,
        "sources": ops_sources,
    }

    ops["sources"] = ops_sources
    ops["fused_schema_version"] = 2
    ops["mesh_context"] = mesh_summary
    ops["llm_context"] = {
        "version": 1,
        "created_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "summary": {
            "spec_overview": spec_overview,
            "mesh_overview": mesh_overview,
            "llm_waam_bytes": llm_payload.get("size_bytes"),
            "pathlib": llm_stats,
            "units": {"config": config.get("units"), "features": (features.get("metadata") or {}).get("units"), "operations": (ops.get("metadata") or {}).get("units")},
            "unit_warnings": unit_warnings,
            "post_contract": post_contract,
            "constraints": constraints,
            "frames": frames,
            "waam_strategy": waam_strategy,
            "validation_rules": validation_rules,
        },
        "config": cfg_meta | {"data": config},
        "features": feats_meta | {"data": features},
        "waam_dsl": llm_payload,
        "mesh": mesh_summary,
        "mesh_raw": mesh_raw,
        "waam_structured": llm_structured,
        "timeline": timeline,
        "toolpath": toolpath,
        "sources": ops_sources | {"mesh_sha1": mesh_summary.get("sha1")},
        "notes": [
            "Use operations + mesh_context as ground truth for geometry and ordering.",
            "WAAM DSL (llm_waam) provides compact layer/path references aligned to mesh indices.",
            "Config and features come directly from standard_waam.json and extracted features.json.",
        ],
        "prompt_view": {
            "counts": {
                "total_features": spec_overview.get("total_features"),
                "total_operations": spec_overview.get("total_operations"),
                "path_count": llm_stats.get("path_count"),
                "layer_count": llm_stats.get("layer_count"),
            },
            "bbox": mesh_overview.get("bbox_min"),
            "bbox_max": mesh_overview.get("bbox_max"),
            "post": post_contract,
            "constraints": constraints,
            "unit_warnings": unit_warnings,
            "frames": frames,
            "waam_strategy": waam_strategy,
        },
    }

    if output_path:
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = get_operations_with_mesh_path(root, prefer_existing=False, create=True)

    target.write_text(json.dumps(ops, indent=2), encoding="utf-8")
    return target


__all__ = ["FusionError", "fuse_operations_with_mesh"]
