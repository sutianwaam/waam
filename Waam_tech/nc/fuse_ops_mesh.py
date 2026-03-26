"""Helpers to build a single LLM-ready JSON with ops, config, features, WAAM DSL, and mesh context."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from ..core.config_loader import resolve_standard_config_path
from ..core.paths import (
    _output_root_for,
    get_features_path,
    get_operations_with_mesh_path,
)
from ..llm_agent.api import SpecLoadError, summarize_mesh_context, summarize_spec
from ..operations.spiral_stc import spiral_coverage_from_polygon
from .contour_clip import clip_paths_to_contours, point_in_polygon


class FusionError(RuntimeError):
    """Raised when WAAM operations + mesh fusion cannot complete."""


Point2D = Tuple[float, float]
Segment2D = Tuple[Point2D, Point2D]


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
) -> list[Segment2D]:
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

    segments: list[Segment2D] = []
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


def _polygon_area(loop: Sequence[Point2D]) -> float:
    if len(loop) < 3:
        return 0.0
    area = 0.0
    pts = list(loop)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        area += x1 * y2 - x2 * y1
    return 0.5 * area


def _loop_centroid(loop: Sequence[Point2D]) -> Point2D:
    pts = list(loop[:-1]) if len(loop) >= 2 and loop[0] == loop[-1] else list(loop)
    if not pts:
        return (0.0, 0.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _path_role(path_data: Mapping[str, object]) -> str:
    return str(path_data.get("role_hint") or "").strip().upper()


def _closed_loop_points(path_data: Mapping[str, object]) -> list[Point2D]:
    pts = path_data.get("points_xyz") or path_data.get("points") or []
    return _ensure_closed_loop_2d(pts)


def _layer_contours(
    layer: Mapping[str, object],
    paths: Mapping[str, Mapping[str, object]],
) -> tuple[list[tuple[str, list[Point2D]]], list[tuple[str, list[Point2D]]], list[str]]:
    outer_items: list[tuple[str, list[Point2D]]] = []
    inner_items: list[tuple[str, list[Point2D]]] = []
    passthrough_ids: list[str] = []
    for path_id in list(layer.get("path_ids") or []):
        pdata = paths.get(str(path_id)) or {}
        role = _path_role(pdata)
        loop = _closed_loop_points(pdata)
        if role == "OUTER" and loop:
            outer_items.append((str(path_id), loop))
        elif role in {"INNER", "HOLE"} and loop:
            inner_items.append((str(path_id), loop))
        else:
            passthrough_ids.append(str(path_id))
    return outer_items, inner_items, passthrough_ids


def _has_explicit_infill(paths: Mapping[str, Mapping[str, object]]) -> bool:
    return any(
        _path_role(pdata) == "INFILL" and str(pdata.get("id") or "") not in {"P_BASE_FILL"}
        for pdata in paths.values()
    )


def _global_thin_wall_signal(features: Mapping[str, object]) -> bool:
    metadata = (features.get("metadata") or {}) if isinstance(features, Mapping) else {}
    mesh_analysis = metadata.get("mesh_analysis") or {}
    thin = mesh_analysis.get("thin_walls") or {}
    samples = thin.get("samples") or []
    return bool(samples)


def _loop_vertices(loop: Sequence[Point2D]) -> list[Point2D]:
    pts = list(loop)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        return pts[:-1]
    return pts


def _region_bbox(loop: Sequence[Point2D]) -> tuple[float, float, float, float]:
    pts = _loop_vertices(loop)
    if not pts:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_contains(bbox_xy: tuple[float, float, float, float], point: Point2D, *, tol: float = 1e-6) -> bool:
    xmin, ymin, xmax, ymax = bbox_xy
    return (xmin - tol) <= point[0] <= (xmax + tol) and (ymin - tol) <= point[1] <= (ymax + tol)


def _thin_wall_samples_for_bbox(features: Mapping[str, object], bbox_xy: tuple[float, float, float, float]) -> list[Mapping[str, object]]:
    metadata = (features.get("metadata") or {}) if isinstance(features, Mapping) else {}
    mesh_analysis = metadata.get("mesh_analysis") or {}
    thin = mesh_analysis.get("thin_walls") or {}
    matched: list[Mapping[str, object]] = []
    for sample in thin.get("samples") or []:
        center = sample.get("center") if isinstance(sample, Mapping) else None
        if not isinstance(center, Sequence) or len(center) < 2:
            continue
        try:
            point = (float(center[0]), float(center[1]))
        except Exception:
            continue
        if _bbox_contains(bbox_xy, point):
            matched.append(sample)
    return matched


def _distance_sq(a: Point2D, b: Point2D) -> float:
    dx = float(a[0]) - float(b[0])
    dy = float(a[1]) - float(b[1])
    return dx * dx + dy * dy


def _distance(a: Point2D, b: Point2D) -> float:
    return math.sqrt(_distance_sq(a, b))


def _iter_loop_segments(loop: Sequence[Point2D]) -> list[tuple[int, Point2D, Point2D]]:
    pts = _ensure_closed_loop_2d(loop)
    if len(pts) < 4:
        return []
    return [(idx, pts[idx], pts[idx + 1]) for idx in range(len(pts) - 1)]


def _cross2(a: Point2D, b: Point2D) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _ray_segment_distance(origin: Point2D, direction: Point2D, a: Point2D, b: Point2D) -> Optional[float]:
    ox, oy = origin
    dx, dy = direction
    sx = b[0] - a[0]
    sy = b[1] - a[1]
    denom = _cross2((dx, dy), (sx, sy))
    if abs(denom) < 1e-9:
        return None
    qpx = a[0] - ox
    qpy = a[1] - oy
    t = _cross2((qpx, qpy), (sx, sy)) / denom
    u = _cross2((qpx, qpy), (dx, dy)) / denom
    if t > 1e-4 and -1e-6 <= u <= 1.0 + 1e-6:
        return float(t)
    return None


def _estimate_region_thickness_stats(
    outer_loop: Sequence[Point2D],
    hole_loops: Sequence[Sequence[Point2D]],
    *,
    sample_step_mm: float,
    max_samples: int,
) -> Mapping[str, object]:
    outer = _ensure_closed_loop_2d(outer_loop)
    holes = [_ensure_closed_loop_2d(loop) for loop in hole_loops if _ensure_closed_loop_2d(loop)]
    if len(outer) < 4 or sample_step_mm <= 0.0 or max_samples <= 0:
        return {"sample_count": 0, "min_mm": None, "p10_mm": None, "avg_mm": None, "samples": []}

    outer_poly = _loop_vertices(outer)
    hole_polys = [_loop_vertices(loop) for loop in holes]
    orientation = 1.0 if _polygon_area(outer) >= 0.0 else -1.0
    samples: list[dict[str, float]] = []

    for edge_idx, start, end in _iter_loop_segments(outer):
        if len(samples) >= max_samples:
            break
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        edge_len = math.hypot(dx, dy)
        if edge_len <= 1e-9:
            continue
        nx = -dy / edge_len
        ny = dx / edge_len
        if orientation < 0.0:
            nx = -nx
            ny = -ny
        steps = max(1, int(math.ceil(edge_len / max(sample_step_mm, 1e-3))))
        for step in range(steps):
            if len(samples) >= max_samples:
                break
            t_edge = (step + 0.5) / steps
            px = start[0] + dx * t_edge
            py = start[1] + dy * t_edge
            probe = (px + nx * 1e-3, py + ny * 1e-3)
            if not point_in_polygon(probe[0], probe[1], outer_poly) or any(
                point_in_polygon(probe[0], probe[1], hole_poly)
                for hole_poly in hole_polys
            ):
                nx = -nx
                ny = -ny
                probe = (px + nx * 1e-3, py + ny * 1e-3)
                if not point_in_polygon(probe[0], probe[1], outer_poly) or any(
                    point_in_polygon(probe[0], probe[1], hole_poly)
                    for hole_poly in hole_polys
                ):
                    continue

            distances: list[float] = []
            for other_idx, a, b in _iter_loop_segments(outer):
                if other_idx == edge_idx:
                    continue
                dist = _ray_segment_distance((px, py), (nx, ny), a, b)
                if dist is not None:
                    distances.append(dist)
            for hole in holes:
                for _hole_idx, a, b in _iter_loop_segments(hole):
                    dist = _ray_segment_distance((px, py), (nx, ny), a, b)
                    if dist is not None:
                        distances.append(dist)
            if not distances:
                continue
            thickness = min(distances)
            samples.append(
                {
                    "x": float(px),
                    "y": float(py),
                    "thickness_mm": float(thickness),
                    "normal_x": float(nx),
                    "normal_y": float(ny),
                }
            )

    if not samples:
        return {"sample_count": 0, "min_mm": None, "p10_mm": None, "avg_mm": None, "samples": []}

    values = sorted(float(sample["thickness_mm"]) for sample in samples)
    p10_idx = min(len(values) - 1, max(0, int(math.floor((len(values) - 1) * 0.1))))
    return {
        "sample_count": len(samples),
        "min_mm": float(values[0]),
        "p10_mm": float(values[p10_idx]),
        "avg_mm": float(sum(values) / len(values)),
        "samples": samples,
    }


def _detect_corner_hotspots(
    loop: Sequence[Point2D],
    *,
    corner_angle_deg: float,
    corner_clearance_mm: float,
) -> list[dict[str, object]]:
    pts = _loop_vertices(loop)
    if len(pts) < 3 or corner_angle_deg <= 0.0 or corner_clearance_mm <= 0.0:
        return []
    hotspots: list[dict[str, object]] = []
    total = len(pts)
    for idx in range(total):
        prev_pt = pts[(idx - 1) % total]
        cur_pt = pts[idx]
        next_pt = pts[(idx + 1) % total]
        incoming = (cur_pt[0] - prev_pt[0], cur_pt[1] - prev_pt[1])
        outgoing = (next_pt[0] - cur_pt[0], next_pt[1] - cur_pt[1])
        len_in = math.hypot(incoming[0], incoming[1])
        len_out = math.hypot(outgoing[0], outgoing[1])
        if len_in <= 1e-9 or len_out <= 1e-9:
            continue
        dot = max(-1.0, min(1.0, (incoming[0] * outgoing[0] + incoming[1] * outgoing[1]) / (len_in * len_out)))
        turn_angle = math.degrees(math.acos(dot))
        if turn_angle + 1e-6 < corner_angle_deg:
            continue
        hotspots.append(
            {
                "kind": "corner",
                "x": float(cur_pt[0]),
                "y": float(cur_pt[1]),
                "radius_mm": float(corner_clearance_mm),
                "turn_angle_deg": float(turn_angle),
            }
        )
    return hotspots


def _is_convex_loop(loop: Sequence[Point2D]) -> bool:
    pts = _loop_vertices(loop)
    if len(pts) < 4:
        return True
    sign = 0
    total = len(pts)
    for idx in range(total):
        p0 = pts[idx]
        p1 = pts[(idx + 1) % total]
        p2 = pts[(idx + 2) % total]
        cross = _cross2((p1[0] - p0[0], p1[1] - p0[1]), (p2[0] - p1[0], p2[1] - p1[1]))
        if abs(cross) <= 1e-9:
            continue
        current = 1 if cross > 0.0 else -1
        if sign == 0:
            sign = current
        elif current != sign:
            return False
    return True


def _detect_branch_junction_hotspots(
    outer_loop: Sequence[Point2D],
    hole_loops: Sequence[Sequence[Point2D]],
    *,
    junction_clearance_mm: float,
    bead_width: float,
    min_neck_width_beads: float,
) -> list[dict[str, object]]:
    pts = _loop_vertices(outer_loop)
    if len(pts) < 4 or junction_clearance_mm <= 0.0:
        return []
    if _is_convex_loop(outer_loop) and len(hole_loops) <= 1:
        return []

    orientation = 1.0 if _polygon_area(outer_loop) >= 0.0 else -1.0
    neck_limit = max(0.0, bead_width * max(0.0, min_neck_width_beads))
    thickness_stats = _estimate_region_thickness_stats(
        outer_loop,
        hole_loops,
        sample_step_mm=max(0.75, bead_width * 0.5),
        max_samples=96,
    )
    thin_samples = [
        sample
        for sample in thickness_stats.get("samples") or []
        if float(sample.get("thickness_mm") or 0.0) <= neck_limit + 1e-6
    ]

    hotspots: list[dict[str, object]] = []
    total = len(pts)
    for idx in range(total):
        prev_pt = pts[(idx - 1) % total]
        cur_pt = pts[idx]
        next_pt = pts[(idx + 1) % total]
        cross = _cross2((cur_pt[0] - prev_pt[0], cur_pt[1] - prev_pt[1]), (next_pt[0] - cur_pt[0], next_pt[1] - cur_pt[1]))
        concave = (cross * orientation) < -1e-6
        if not concave:
            continue
        nearest = min((_distance((cur_pt[0], cur_pt[1]), (sample["x"], sample["y"])) for sample in thin_samples), default=None)
        if neck_limit > 0.0 and nearest is not None and nearest > junction_clearance_mm * 1.5:
            continue
        hotspots.append(
            {
                "kind": "junction",
                "x": float(cur_pt[0]),
                "y": float(cur_pt[1]),
                "radius_mm": float(junction_clearance_mm),
            }
        )
    return hotspots


def _merge_hotspots(*hotspot_groups: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for group in hotspot_groups:
        for hotspot in group:
            try:
                x = float(hotspot.get("x"))
                y = float(hotspot.get("y"))
                radius = float(hotspot.get("radius_mm") or 0.0)
                kind = str(hotspot.get("kind") or "")
            except Exception:
                continue
            existing = next(
                (
                    item
                    for item in merged
                    if item["kind"] == kind and _distance((float(item["x"]), float(item["y"])), (x, y)) <= max(1.0, radius * 0.35)
                ),
                None,
            )
            if existing is None:
                merged.append(dict(hotspot))
            else:
                existing["radius_mm"] = max(float(existing.get("radius_mm") or 0.0), radius)
    return merged


def _build_layer_regions(
    layer: Mapping[str, object],
    paths: Mapping[str, Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[str]]:
    outer_items, inner_items, passthrough_ids = _layer_contours(layer, paths)
    regions: list[dict[str, object]] = []
    pending_inners = list(inner_items)

    for region_idx, (outer_path_id, outer_loop) in enumerate(outer_items):
        outer_poly = _loop_vertices(outer_loop)
        hole_items: list[tuple[str, list[Point2D]]] = []
        still_pending: list[tuple[str, list[Point2D]]] = []
        for item in pending_inners:
            cx, cy = _loop_centroid(item[1])
            if point_in_polygon(cx, cy, outer_poly):
                hole_items.append(item)
            else:
                still_pending.append(item)
        pending_inners = still_pending
        region = {
            "id": f"R{region_idx}",
            "outer_path_id": outer_path_id,
            "outer_loop": list(outer_loop),
            "inner_items": [(path_id, list(loop)) for path_id, loop in hole_items],
            "hole_loops": [list(loop) for _path_id, loop in hole_items],
            "frame": (paths.get(outer_path_id) or {}).get("frame", layer.get("frame", "mesh_frame")),
            "bbox_xy": _region_bbox(outer_loop),
            "passthrough_ids": [],
        }
        regions.append(region)

    global_passthrough: list[str] = []
    for path_id in passthrough_ids:
        pdata = paths.get(path_id) or {}
        pts = pdata.get("points_xyz") or pdata.get("points") or []
        point = None
        for entry in pts:
            if isinstance(entry, Sequence) and len(entry) >= 2:
                try:
                    point = (float(entry[0]), float(entry[1]))
                    break
                except Exception:
                    point = None
        if point is None:
            global_passthrough.append(path_id)
            continue
        assigned = False
        for region in regions:
            if point_in_polygon(point[0], point[1], _loop_vertices(region["outer_loop"])):
                region["passthrough_ids"].append(path_id)
                assigned = True
                break
        if not assigned:
            global_passthrough.append(path_id)

    for path_id, loop in pending_inners:
        global_passthrough.append(path_id)

    return regions, global_passthrough


def _count_region_holes_in_outer(outer_loop: Sequence[Point2D], regions: Sequence[Mapping[str, object]]) -> int:
    outer_poly = _loop_vertices(outer_loop)
    count = 0
    for region in regions:
        for _path_id, hole_loop in region.get("inner_items") or []:
            cx, cy = _loop_centroid(hole_loop)
            if point_in_polygon(cx, cy, outer_poly):
                count += 1
    return count


def _classify_region_mode(
    region: Mapping[str, object],
    *,
    bead_width: float,
    spacing: float,
    topo_cfg: Mapping[str, object],
    global_thin: bool,
) -> str:
    thin_cfg = topo_cfg.get("thin_wall_detection") if isinstance(topo_cfg.get("thin_wall_detection"), Mapping) else {}
    detection_enabled = bool(thin_cfg.get("enabled", True))
    single_track_max = float(thin_cfg.get("single_track_max_width_beads") or 1.35)
    dual_track_max = float(thin_cfg.get("dual_track_max_width_beads") or 2.35)
    thin_wall_area_ratio_max = float(topo_cfg.get("thin_wall_area_ratio_max") or 0.45)
    p10_mm = region.get("thickness_stats", {}).get("p10_mm") if isinstance(region.get("thickness_stats"), Mapping) else None
    try:
        p10_val = float(p10_mm) if p10_mm is not None else None
    except Exception:
        p10_val = None

    if bool(region.get("protect_voids")) or bool(region.get("topology_transition")):
        return "transition_region"
    if detection_enabled and p10_val is not None:
        if p10_val <= bead_width * max(0.1, single_track_max):
            return "single_track_wall"
        if p10_val <= bead_width * max(0.1, dual_track_max):
            return "dual_track_wall"

    mesh_thin_count = int(region.get("mesh_thin_sample_count") or 0)
    material_ratio = float(region.get("material_ratio") or 1.0)
    if material_ratio <= thin_wall_area_ratio_max or (global_thin and mesh_thin_count > 0):
        if p10_val is not None and p10_val <= bead_width * max(0.1, single_track_max):
            return "single_track_wall"
        return "dual_track_wall"
    _ = spacing
    return "bulk_fill"


def _points_xyz(path_data: Mapping[str, object]) -> list[list[float]]:
    pts = path_data.get("points_xyz") or path_data.get("points") or []
    out: list[list[float]] = []
    for entry in pts:
        if not isinstance(entry, Sequence) or len(entry) < 2:
            continue
        try:
            x = float(entry[0])
            y = float(entry[1])
            z = float(entry[2]) if len(entry) >= 3 else 0.0
        except Exception:
            continue
        out.append([x, y, z])
    return out


def _is_closed_points_xyz(points: Sequence[Sequence[float]], tol: float = 1e-6) -> bool:
    if len(points) < 3:
        return False
    return abs(float(points[0][0]) - float(points[-1][0])) <= tol and abs(float(points[0][1]) - float(points[-1][1])) <= tol


def _closest_point_index_xyz(points: Sequence[Sequence[float]], target: Point2D) -> Optional[int]:
    if not points:
        return None
    core = list(points[:-1]) if _is_closed_points_xyz(points) else list(points)
    if not core:
        return None
    tx, ty = target
    best_idx = 0
    best_dist = None
    for idx, pt in enumerate(core):
        dist = (float(pt[0]) - tx) ** 2 + (float(pt[1]) - ty) ** 2
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


def _rotate_closed_path_to_target(points: Sequence[Sequence[float]], target: Point2D) -> list[list[float]]:
    if not _is_closed_points_xyz(points):
        return [list(pt) for pt in points]
    core = [list(pt) for pt in points[:-1]]
    idx = _closest_point_index_xyz(core, target)
    if idx is None or idx <= 0:
        rotated = core
    else:
        rotated = core[idx:] + core[:idx]
    if rotated:
        rotated.append(list(rotated[0]))
    return rotated


def _reverse_path_points(points: Sequence[Sequence[float]]) -> list[list[float]]:
    if _is_closed_points_xyz(points):
        core = [list(pt) for pt in points[:-1]]
        core.reverse()
        if core:
            core.append(list(core[0]))
        return core
    out = [list(pt) for pt in points]
    out.reverse()
    return out


def _path_start_heading(points: Sequence[Sequence[float]]) -> Optional[float]:
    if len(points) < 2:
        return None
    a = points[0]
    b = points[1]
    dx = float(b[0]) - float(a[0])
    dy = float(b[1]) - float(a[1])
    if abs(dx) <= 1e-9 and abs(dy) <= 1e-9:
        return None
    return math.atan2(dy, dx)


def _path_end_heading(points: Sequence[Sequence[float]]) -> Optional[float]:
    if len(points) < 2:
        return None
    a = points[-2]
    b = points[-1]
    dx = float(b[0]) - float(a[0])
    dy = float(b[1]) - float(a[1])
    if abs(dx) <= 1e-9 and abs(dy) <= 1e-9:
        return None
    return math.atan2(dy, dx)


def _path_transition_cost(
    prev_end: Optional[Point2D],
    candidate_points: Sequence[Sequence[float]],
    *,
    previous_heading: Optional[float],
    restart_penalty_mm: float,
    heading_change_penalty_mm: float,
    region_switch_penalty_mm: float,
) -> float:
    if not candidate_points:
        return float("inf")
    cost = 0.0
    if prev_end is not None:
        start_pt = (float(candidate_points[0][0]), float(candidate_points[0][1]))
        jump = _distance(prev_end, start_pt)
        cost += jump + max(0.0, restart_penalty_mm) + max(0.0, region_switch_penalty_mm)
    if previous_heading is not None:
        candidate_heading = _path_start_heading(candidate_points)
        if candidate_heading is not None:
            delta = abs(candidate_heading - previous_heading)
            delta = min(delta, abs(2.0 * math.pi - delta))
            cost += max(0.0, heading_change_penalty_mm) * (delta / math.pi)
    return cost


def _path_role_rank(path_data: Mapping[str, object]) -> int:
    role = _path_role(path_data)
    if role == "OUTER":
        return 0
    if role in {"INNER", "HOLE"}:
        return 1
    if role == "INFILL":
        return 3
    return 2


def _order_layer_paths_continuously(
    layer: Mapping[str, object],
    paths: Dict[str, Mapping[str, object]],
    *,
    continuity_cfg: Mapping[str, object],
) -> tuple[list[str], Mapping[str, object]]:
    original_ids = [str(pid) for pid in (layer.get("path_ids") or []) if str(pid) in paths]
    summary = {
        "ordered_path_ids": list(original_ids),
        "rotated_paths": [],
        "reversed_paths": [],
        "estimated_restarts": max(0, len(original_ids) - 1),
        "non_deposition_jump_mm": 0.0,
    }
    if not original_ids:
        return [], summary
    if not bool(continuity_cfg.get("enabled", True)):
        return original_ids, summary

    rotate_closed = bool(continuity_cfg.get("rotate_closed_paths", True))
    allow_reverse = bool(continuity_cfg.get("allow_reverse_open_paths", True))
    restart_penalty = float(continuity_cfg.get("restart_penalty_mm") or 0.0)
    heading_penalty = float(continuity_cfg.get("heading_change_penalty_mm") or 0.0)
    region_switch_penalty = float(continuity_cfg.get("region_switch_penalty_mm") or 0.0)

    role_buckets: dict[int, list[tuple[int, str]]] = {}
    for index, path_id in enumerate(original_ids):
        role_buckets.setdefault(_path_role_rank(paths.get(path_id) or {}), []).append((index, path_id))

    ordered_ids: list[str] = []
    rotated_paths: list[str] = []
    reversed_paths: list[str] = []
    total_jump = 0.0
    prev_end: Optional[Point2D] = None
    prev_heading: Optional[float] = None
    prev_region: Optional[str] = None

    for rank in sorted(role_buckets):
        remaining = list(role_buckets[rank])
        while remaining:
            best_choice = None
            best_score = None
            for original_index, path_id in remaining:
                pdata = dict(paths.get(path_id) or {})
                base_points = _points_xyz(pdata)
                if len(base_points) < 2:
                    continue
                tags = pdata.get("planner_tags") if isinstance(pdata.get("planner_tags"), Mapping) else {}
                candidates: list[tuple[list[list[float]], bool, bool]] = []
                if _is_closed_points_xyz(base_points):
                    rotated = _rotate_closed_path_to_target(base_points, prev_end) if (rotate_closed and prev_end is not None) else [list(pt) for pt in base_points]
                    candidates.append((rotated, rotated != base_points, False))
                else:
                    candidates.append(([list(pt) for pt in base_points], False, False))
                    reversible = bool(tags.get("continuity_reversible", True))
                    if allow_reverse and reversible:
                        candidates.append((_reverse_path_points(base_points), False, True))
                for candidate_points, did_rotate, did_reverse in candidates:
                    candidate_region = str(tags.get("region_id") or "") or None
                    penalty = region_switch_penalty if prev_region and candidate_region and candidate_region != prev_region else 0.0
                    score = _path_transition_cost(
                        prev_end,
                        candidate_points,
                        previous_heading=prev_heading,
                        restart_penalty_mm=restart_penalty,
                        heading_change_penalty_mm=heading_penalty,
                        region_switch_penalty_mm=penalty,
                    )
                    tie_break = (score, original_index, path_id)
                    if best_score is None or tie_break < best_score:
                        best_score = tie_break
                        best_choice = (original_index, path_id, candidate_points, did_rotate, did_reverse, candidate_region)
            if best_choice is None:
                ordered_ids.extend(path_id for _idx, path_id in remaining)
                break

            original_index, chosen_id, chosen_points, did_rotate, did_reverse, chosen_region = best_choice
            pdata = dict(paths.get(chosen_id) or {})
            pdata["points_xyz"] = [list(pt) for pt in chosen_points]
            tags = dict(pdata.get("planner_tags") or {})
            tags["continuity_reversible"] = bool(tags.get("continuity_reversible", not _is_closed_points_xyz(chosen_points)))
            tags["continuity_rotated"] = bool(did_rotate)
            tags["continuity_reversed"] = bool(did_reverse)
            pdata["planner_tags"] = tags
            paths[chosen_id] = pdata

            if prev_end is not None:
                jump = _distance(prev_end, (float(chosen_points[0][0]), float(chosen_points[0][1])))
                total_jump += jump
            if did_rotate:
                rotated_paths.append(chosen_id)
            if did_reverse:
                reversed_paths.append(chosen_id)
            ordered_ids.append(chosen_id)
            prev_end = (float(chosen_points[-1][0]), float(chosen_points[-1][1]))
            prev_heading = _path_end_heading(chosen_points)
            prev_region = chosen_region
            remaining = [item for item in remaining if item[1] != chosen_id]

    summary = {
        "ordered_path_ids": ordered_ids,
        "rotated_paths": rotated_paths,
        "reversed_paths": reversed_paths,
        "estimated_restarts": max(0, len(ordered_ids) - 1),
        "non_deposition_jump_mm": round(total_jump, 6),
    }
    return ordered_ids, summary


def _point_in_hotspot(point: Point2D, hotspots: Sequence[Mapping[str, object]]) -> bool:
    for hotspot in hotspots:
        try:
            radius = float(hotspot.get("radius_mm") or 0.0)
            hx = float(hotspot.get("x"))
            hy = float(hotspot.get("y"))
        except Exception:
            continue
        if radius > 0.0 and _distance(point, (hx, hy)) <= radius + 1e-6:
            return True
    return False


def _build_region_infill_paths(
    region: Mapping[str, object],
    *,
    layer_index: int,
    angle_deg: float,
    spacing: float,
    strategy: str,
    topo_cfg: Mapping[str, object],
    used_ids: set[str],
    max_nodes: int,
    max_segments: int,
) -> tuple[list[str], dict[str, dict[str, object]]]:
    if str(region.get("region_mode") or "") != "bulk_fill":
        return [], {}

    outer_loop = region.get("outer_loop") or []
    hole_loops = region.get("hole_loops") or []
    outer_path_id = str(region.get("outer_path_id") or f"R{layer_index}")
    frame = str(region.get("frame") or "mesh_frame")
    hotspots = list(region.get("hotspots") or [])
    hotspot_cfg = topo_cfg.get("hotspots") if isinstance(topo_cfg.get("hotspots"), Mapping) else {}
    suppress_hotspots = bool(hotspot_cfg.get("suppress_infill_near_hotspots", True))
    hotspot_kinds = {str(item.get("kind") or "") for item in hotspots}

    new_ids: list[str] = []
    new_paths: dict[str, dict[str, object]] = {}
    if strategy in {"spiral_stc", "spiral", "spiral_spanning_tree"} and not hole_loops and (not hotspots or hotspot_kinds <= {"corner"}):
        spiral_pts = spiral_coverage_from_polygon(list(outer_loop)[:-1], spacing=spacing, max_merged_nodes=max_nodes)
        if spiral_pts:
            counter = 1
            path_id = f"TOPO_SPIRAL_{layer_index}_{counter}"
            while path_id in used_ids:
                counter += 1
                path_id = f"TOPO_SPIRAL_{layer_index}_{counter}"
            used_ids.add(path_id)
            new_ids.append(path_id)
            new_paths[path_id] = {
                "id": path_id,
                "role_hint": "INFILL",
                "frame": frame,
                "closed": False,
                "points_xyz": [[float(x), float(y), 0.0] for x, y in spiral_pts],
                "planner_tags": {
                    "region_id": region.get("id"),
                    "region_mode": region.get("region_mode"),
                    "hotspot_kinds": sorted(k for k in hotspot_kinds if k),
                    "continuity_reversible": True,
                },
            }
            return new_ids, new_paths

    outer_segments = _hatch_segments_from_polygon(
        [[x, y, 0.0] for x, y in outer_loop],
        spacing=spacing,
        angle_deg=angle_deg,
    )
    clipped_segments = clip_paths_to_contours(
        outer_segments,
        list(outer_loop),
        [list(loop) for loop in hole_loops],
    )
    segment_count = 0
    infill_counter = 0
    for seg_idx, ((sx, sy), (ex, ey)) in enumerate(clipped_segments, start=1):
        if segment_count >= max_segments:
            break
        midpoint = ((sx + ex) * 0.5, (sy + ey) * 0.5)
        if suppress_hotspots and hotspots and _point_in_hotspot(midpoint, hotspots):
            continue
        infill_counter += 1
        path_id = f"{outer_path_id}_TOPO_INFILL_{layer_index}_{seg_idx}_{infill_counter}"
        while path_id in used_ids:
            infill_counter += 1
            path_id = f"{outer_path_id}_TOPO_INFILL_{layer_index}_{seg_idx}_{infill_counter}"
        used_ids.add(path_id)
        new_ids.append(path_id)
        new_paths[path_id] = {
            "id": path_id,
            "role_hint": "INFILL",
            "frame": frame,
            "closed": False,
            "points_xyz": [[sx, sy, 0.0], [ex, ey, 0.0]],
            "planner_tags": {
                "region_id": region.get("id"),
                "region_mode": region.get("region_mode"),
                "hotspot_kinds": sorted(k for k in hotspot_kinds if k),
                "continuity_reversible": True,
            },
        }
        segment_count += 1

    return new_ids, new_paths


def _layered_topology_planner(
    llm_structured: Dict[str, object],
    *,
    config: Mapping[str, object],
    features: Mapping[str, object],
    max_segments_per_layer: int = 800,
) -> Dict[str, object]:
    paths = dict(llm_structured.get("paths") or {})
    layers = list(llm_structured.get("layers") or [])
    if not paths or not layers or _has_explicit_infill(paths):
        return llm_structured

    process = (config.get("process") or {})
    dep = process.get("deposition") or {}
    path_cfg = process.get("path") or {}
    topo_cfg = path_cfg.get("topology_sensitive") if isinstance(path_cfg.get("topology_sensitive"), Mapping) else {}

    bead_width = float(dep.get("bead_width_mm") or dep.get("bead_width") or 6.0)
    overlap_pct = float(dep.get("overlap_pct") or 30.0)
    spacing = max(0.5, bead_width * max(0.1, 1.0 - overlap_pct / 100.0))
    angle0 = float(path_cfg.get("angle_deg") or 0.0)
    angle_step = float(path_cfg.get("angle_step_deg") or 90.0)
    lookahead_layers = max(0, int(topo_cfg.get("void_lookahead_layers") or 2))
    thin_wall_area_ratio_max = float(topo_cfg.get("thin_wall_area_ratio_max") or 0.45)
    suppress_on_transition = bool(topo_cfg.get("suppress_infill_on_transition", True))
    max_nodes = int(path_cfg.get("spiral_max_nodes") or 20000)
    strategy = _infill_strategy(config)
    global_thin = _global_thin_wall_signal(features)
    thin_cfg = topo_cfg.get("thin_wall_detection") if isinstance(topo_cfg.get("thin_wall_detection"), Mapping) else {}
    hotspot_cfg = topo_cfg.get("hotspots") if isinstance(topo_cfg.get("hotspots"), Mapping) else {}
    continuity_cfg = topo_cfg.get("continuity") if isinstance(topo_cfg.get("continuity"), Mapping) else {}
    sample_step_mm = float(thin_cfg.get("sample_step_mm") or 1.5)
    max_samples_per_region = int(thin_cfg.get("max_samples_per_region") or 96)
    corner_angle_deg = float(hotspot_cfg.get("corner_angle_deg") or 45.0)
    corner_clearance_mm = float(hotspot_cfg.get("corner_clearance_mm") or max(bead_width, spacing * 1.25))
    junction_clearance_mm = float(hotspot_cfg.get("junction_clearance_mm") or max(bead_width * 1.5, spacing * 1.5))
    min_neck_width_beads = float(hotspot_cfg.get("min_neck_width_beads") or 2.0)

    layer_region_cache: list[list[dict[str, object]]] = []
    layer_passthrough_cache: list[list[str]] = []
    layer_inner_counts: list[int] = []
    for layer in layers:
        regions, passthrough = _build_layer_regions(layer, paths)
        layer_region_cache.append(regions)
        layer_passthrough_cache.append(passthrough)
        layer_inner_counts.append(sum(len(region.get("inner_items") or []) for region in regions))

    used_ids = set(paths.keys())

    new_layers = []
    for idx, layer in enumerate(layers):
        regions = layer_region_cache[idx]
        passthrough_ids = list(layer_passthrough_cache[idx])
        if not regions:
            new_layers.append(layer)
            continue

        layer_index = int(layer.get("i", idx) or idx)
        angle_deg = angle0 + layer_index * angle_step
        layer_outer_area = 0.0
        layer_inner_area = 0.0
        layer_hotspots: list[dict[str, object]] = []
        region_modes: dict[str, str] = {}
        ordered_ids: list[str] = []
        new_infill_ids: list[str] = []
        layer_allow_infill = False
        layer_thin_wall = False

        for region in regions:
            outer_loop = list(region.get("outer_loop") or [])
            hole_loops = [list(loop) for loop in region.get("hole_loops") or []]
            current_inner_count = len(region.get("inner_items") or [])
            prev_inner_count = _count_region_holes_in_outer(region["outer_loop"], layer_region_cache[idx - 1]) if idx > 0 else current_inner_count
            next_inner_count = _count_region_holes_in_outer(region["outer_loop"], layer_region_cache[idx + 1]) if idx + 1 < len(layer_region_cache) else current_inner_count
            future_inner = any(
                _count_region_holes_in_outer(region["outer_loop"], layer_region_cache[j]) > 0
                for j in range(idx + 1, min(len(layer_region_cache), idx + 1 + lookahead_layers))
            )
            outer_area = abs(_polygon_area(outer_loop))
            inner_area = sum(abs(_polygon_area(loop)) for loop in hole_loops)
            material_ratio = ((outer_area - inner_area) / outer_area) if outer_area > 1e-9 else 1.0
            topology_transition = (
                current_inner_count != prev_inner_count
                or current_inner_count != next_inner_count
                or future_inner
            )
            protect_voids = future_inner and current_inner_count == 0

            thickness_stats = _estimate_region_thickness_stats(
                outer_loop,
                hole_loops,
                sample_step_mm=sample_step_mm,
                max_samples=max_samples_per_region,
            )
            mesh_thin_samples = _thin_wall_samples_for_bbox(features, region["bbox_xy"])
            region["material_ratio"] = material_ratio
            region["thickness_stats"] = thickness_stats
            region["mesh_thin_sample_count"] = len(mesh_thin_samples)
            region["topology_transition"] = bool(topology_transition and suppress_on_transition)
            region["protect_voids"] = bool(protect_voids)
            region_mode = _classify_region_mode(
                region,
                bead_width=bead_width,
                spacing=spacing,
                topo_cfg=topo_cfg,
                global_thin=global_thin,
            )
            region["region_mode"] = region_mode
            region_modes[str(region["id"])] = region_mode
            if region_mode != "bulk_fill":
                layer_thin_wall = True

            corner_hotspots = _detect_corner_hotspots(
                outer_loop,
                corner_angle_deg=corner_angle_deg,
                corner_clearance_mm=corner_clearance_mm,
            )
            branch_hotspots = _detect_branch_junction_hotspots(
                outer_loop,
                hole_loops,
                junction_clearance_mm=junction_clearance_mm,
                bead_width=bead_width,
                min_neck_width_beads=min_neck_width_beads,
            )
            region_hotspots = _merge_hotspots(corner_hotspots, branch_hotspots)
            for hotspot in region_hotspots:
                hotspot["region_id"] = region["id"]
            region["hotspots"] = region_hotspots
            layer_hotspots.extend(region_hotspots)

            contour_hotspot_kinds = sorted({str(item.get("kind") or "") for item in region_hotspots if str(item.get("kind") or "")})
            for path_id in [region.get("outer_path_id"), *[path_id for path_id, _loop in region.get("inner_items") or []], *list(region.get("passthrough_ids") or [])]:
                if not path_id or str(path_id) not in paths:
                    continue
                pdata = dict(paths.get(str(path_id)) or {})
                tags = dict(pdata.get("planner_tags") or {})
                tags.update(
                    {
                        "region_id": region["id"],
                        "region_mode": region_mode,
                        "hotspot_kinds": contour_hotspot_kinds,
                        "continuity_reversible": not bool(pdata.get("closed")),
                    }
                )
                pdata["planner_tags"] = tags
                paths[str(path_id)] = pdata

            ordered_ids.append(str(region["outer_path_id"]))
            ordered_ids.extend(str(path_id) for path_id, _loop in region.get("inner_items") or [])
            ordered_ids.extend(str(path_id) for path_id in region.get("passthrough_ids") or [])
            layer_outer_area += outer_area
            layer_inner_area += inner_area

            infill_ids, infill_paths = _build_region_infill_paths(
                region,
                layer_index=layer_index,
                angle_deg=angle_deg,
                spacing=spacing,
                strategy=strategy,
                topo_cfg=topo_cfg,
                used_ids=used_ids,
                max_nodes=max_nodes,
                max_segments=max(0, max_segments_per_layer - len(new_infill_ids)),
            )
            if infill_ids:
                layer_allow_infill = True
                new_infill_ids.extend(infill_ids)
                for path_id, pdata in infill_paths.items():
                    paths[path_id] = pdata

        ordered_ids.extend(
            path_id
            for path_id in passthrough_ids
            if _path_role(paths.get(path_id) or {}) != "INFILL"
        )

        layer_copy = dict(layer)
        layer_copy["path_ids"] = ordered_ids + new_infill_ids
        layer_copy["planner_mode"] = "layered_topology"
        continuity_ids, continuity_summary = _order_layer_paths_continuously(
            layer_copy,
            paths,
            continuity_cfg=continuity_cfg,
        )
        layer_copy["path_ids"] = continuity_ids
        material_ratio = ((layer_outer_area - layer_inner_area) / layer_outer_area) if layer_outer_area > 1e-9 else 1.0
        current_inner_count = layer_inner_counts[idx]
        prev_inner_count = layer_inner_counts[idx - 1] if idx > 0 else current_inner_count
        next_inner_count = layer_inner_counts[idx + 1] if idx + 1 < len(layer_inner_counts) else current_inner_count
        layer_transition = current_inner_count != prev_inner_count or current_inner_count != next_inner_count
        layer_copy["topology"] = {
            "inner_count": current_inner_count,
            "material_ratio": round(material_ratio, 6),
            "thin_wall_like": layer_thin_wall or material_ratio <= thin_wall_area_ratio_max,
            "protect_voids": any(bool(region.get("protect_voids")) for region in regions),
            "topology_transition": layer_transition or any(bool(region.get("topology_transition")) for region in regions),
            "infill_enabled": layer_allow_infill,
            "region_modes": region_modes,
            "hotspots": [
                {
                    "kind": str(hotspot.get("kind") or ""),
                    "region_id": str(hotspot.get("region_id") or ""),
                    "x": round(float(hotspot.get("x") or 0.0), 6),
                    "y": round(float(hotspot.get("y") or 0.0), 6),
                    "radius_mm": round(float(hotspot.get("radius_mm") or 0.0), 6),
                }
                for hotspot in layer_hotspots
            ],
            "continuity": continuity_summary,
        }
        new_layers.append(layer_copy)

    llm_structured["paths"] = paths
    llm_structured["layers"] = new_layers
    llm_structured["planner_mode"] = "layered_topology"
    llm_structured["planner_basis"] = "layer-by-layer material deposition"
    return llm_structured


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


def _planner_mode(config: Mapping[str, object]) -> str:
    path_cfg = (config.get("process") or {}).get("path") or {}
    topo_cfg = path_cfg.get("topology_sensitive") if isinstance(path_cfg.get("topology_sensitive"), Mapping) else {}
    mode = path_cfg.get("planner_mode") or "layered_topology"
    if topo_cfg and topo_cfg.get("enabled") is False:
        return "legacy"
    return str(mode).strip().lower()


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
    planner_mode = _planner_mode(config)
    if planner_mode == "legacy":
        llm_structured = _inject_base_fill(llm_structured)
        strategy = _infill_strategy(config)
        if strategy in {"spiral_stc", "spiral", "spiral_spanning_tree"}:
            llm_structured = _inject_spiral_infill(llm_structured, config=config)
        else:
            llm_structured = _inject_hatch_infill(llm_structured, config=config)
    else:
        llm_structured = _layered_topology_planner(
            llm_structured,
            config=config,
            features=features,
        )

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
        "planner_mode": planner_mode,
        "planner_basis": "layer-by-layer material deposition",
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
