from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import FreeCAD as App  # type: ignore
import Part  # type: ignore

from ..core.hashing import sha256_file, sha256_json, short_hash
from ..core.wcs_resolver import resolve_wcs
from ..nc.contour_clip import clip_paths_to_contours, point_in_polygon
from ..ui.wcs_marker import get_deposition_start
from .format_waam_dsl import write_llm_waam_dsl
from .step_to_waam_dsl import (
    Contour,
    LayerSlice,
    compute_bounding_box,
    generate_layer_slices,
    transform_shape_to_local_frame,
)

Point2D = Tuple[float, float]
Segment2D = Tuple[Point2D, Point2D]

DEFAULT_LAYER_HEIGHT_MM = 1.5
DEFAULT_BEAD_WIDTH_MM = 6.0
DEFAULT_OVERLAP_PCT = 40.0
DEFAULT_DEPOSITION_PATTERN = "contour_hatch"


SLICE_PLAN_GENERATOR_VERSION = "waam-slice-plan/1"


@dataclass(frozen=True)
class SlicePlanArtifacts:
    plan_path: Path
    features_path: Optional[Path]
    llm_waam_path: Optional[Path]
    layer_count: int
    path_count: int
    wcs_source: str = ""
    wcs_warnings: Tuple[str, ...] = ()
    deposition_start_source: str = ""
    manifest_path: Optional[Path] = None


def _copy_shape(shape):
    copier = getattr(shape, "copy", None)
    if callable(copier):
        try:
            return copier()
        except Exception:
            pass
    return shape


def build_compound_shape(objects: Iterable[App.DocumentObject]):
    shapes = []
    for obj in objects:
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if shape.isNull():
                continue
        except Exception:
            continue
        shapes.append(_copy_shape(shape))

    if not shapes:
        raise ValueError("No valid solid shapes were found to build the slice plan.")
    if len(shapes) == 1:
        return shapes[0]
    return Part.makeCompound(shapes)


def _merge_mapping(base: Dict[str, object], override: Mapping[str, object]) -> Dict[str, object]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), Mapping):
            base[key] = _merge_mapping(dict(base.get(key) or {}), value)
        else:
            base[key] = value
    return base


def _vec_list(vec: App.Vector) -> list[float]:
    return [float(vec.x), float(vec.y), float(vec.z)]


def _wcs_metadata(
    wcs_name: str,
    placement: App.Placement,
    *,
    source: str = "",
    warnings: Sequence[str] = (),
) -> Dict[str, object]:
    return {
        "name": wcs_name,
        "origin": [0.0, 0.0, 0.0],
        "axes": {"x": [1.0, 0.0, 0.0], "y": [0.0, 1.0, 0.0], "z": [0.0, 0.0, 1.0]},
        "placement": {
            "origin_world": _vec_list(placement.Base),
            "axes_world": {
                "x": _vec_list(placement.Rotation.multVec(App.Vector(1, 0, 0))),
                "y": _vec_list(placement.Rotation.multVec(App.Vector(0, 1, 0))),
                "z": _vec_list(placement.Rotation.multVec(App.Vector(0, 0, 1))),
            },
        },
        # Where this placement came from ("stored_marker", "path_job", or
        # "identity_fallback") and any resolver warnings, so downstream tools
        # and the planner dialog can surface staleness/misconfiguration
        # instead of silently trusting world coordinates.
        "source": source,
        "warnings": list(warnings),
    }


def _bbox_dict(shape) -> Dict[str, float]:
    xmin, ymin, zmin, xmax, ymax, zmax = compute_bounding_box(shape)
    return {
        "xmin": float(xmin),
        "xmax": float(xmax),
        "ymin": float(ymin),
        "ymax": float(ymax),
        "zmin": float(zmin),
        "zmax": float(zmax),
    }


def _bbox_dimensions(bbox: Mapping[str, object]) -> Dict[str, float]:
    xmin = _float_or_default(bbox.get("xmin"), 0.0)
    xmax = _float_or_default(bbox.get("xmax"), 0.0)
    ymin = _float_or_default(bbox.get("ymin"), 0.0)
    ymax = _float_or_default(bbox.get("ymax"), 0.0)
    zmin = _float_or_default(bbox.get("zmin"), 0.0)
    zmax = _float_or_default(bbox.get("zmax"), 0.0)
    width = max(0.0, xmax - xmin)
    depth = max(0.0, ymax - ymin)
    height = max(0.0, zmax - zmin)
    return {
        "width_mm": width,
        "depth_mm": depth,
        "height_mm": height,
        "max_span_mm": max(width, depth),
        "max_dimension_mm": max(width, depth, height),
        "xy_diagonal_mm": math.hypot(width, depth),
        "xyz_diagonal_mm": math.sqrt((width * width) + (depth * depth) + (height * height)),
    }


def _path_spacing_mm(bead_width_mm: float, overlap_pct: float) -> float:
    return max(0.5, bead_width_mm * max(0.1, 1.0 - overlap_pct / 100.0))


def _as_optional_finite(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _lerp(a: float, b: float, t: float) -> float:
    return float(a) + (float(b) - float(a)) * float(t)


def _auto_scale_driver_value(driver: str, bbox_dims: Mapping[str, object]) -> tuple[str, float]:
    normalized = str(driver or "max_span").strip().lower()
    if normalized in {"height", "bbox_height"}:
        return "height", _float_or_default(bbox_dims.get("height_mm"), 0.0)
    if normalized in {"max_dimension", "bbox_max_dimension"}:
        return "max_dimension", _float_or_default(bbox_dims.get("max_dimension_mm"), 0.0)
    if normalized in {"xy_diagonal", "planar_diagonal"}:
        return "xy_diagonal", _float_or_default(bbox_dims.get("xy_diagonal_mm"), 0.0)
    if normalized in {"xyz_diagonal", "bbox_diagonal"}:
        return "xyz_diagonal", _float_or_default(bbox_dims.get("xyz_diagonal_mm"), 0.0)
    return "max_span", _float_or_default(bbox_dims.get("max_span_mm"), 0.0)


def _apply_scaled_range_values(
    target: Dict[str, object],
    ranges_cfg: Mapping[str, object],
    *,
    blend_factor: float,
) -> Dict[str, float]:
    applied: Dict[str, float] = {}
    for key, raw_cfg in ranges_cfg.items():
        if not isinstance(raw_cfg, Mapping):
            continue
        min_value = _as_optional_finite(raw_cfg.get("at_min_span"))
        max_value = _as_optional_finite(raw_cfg.get("at_max_span"))
        if min_value is None or max_value is None:
            continue
        scaled = _lerp(min_value, max_value, blend_factor)
        target[str(key)] = scaled
        applied[str(key)] = scaled
    return applied


def _apply_auto_scale(
    config: Dict[str, object],
    *,
    bbox: Mapping[str, object],
) -> Dict[str, object]:
    bbox_dims = _bbox_dimensions(bbox)
    report: Dict[str, object] = {
        "enabled": False,
        "applied": False,
        "driver": "max_span",
        "driver_value_mm": 0.0,
        "bbox_dimensions_mm": bbox_dims,
        "applied_values": {"deposition": {}, "path": {}},
    }

    process_cfg = config.get("process")
    if not isinstance(process_cfg, Mapping):
        return report
    auto_cfg = process_cfg.get("auto_scale")
    if not isinstance(auto_cfg, Mapping):
        return report

    report["enabled"] = bool(auto_cfg.get("enabled"))
    if not report["enabled"]:
        return report

    driver, driver_value = _auto_scale_driver_value(str(auto_cfg.get("driver") or "max_span"), bbox_dims)
    report["driver"] = driver
    report["driver_value_mm"] = driver_value

    driver_min = _as_optional_finite(auto_cfg.get("driver_min_mm"))
    if driver_min is None:
        driver_min = _as_optional_finite(auto_cfg.get("span_min_mm"))
    driver_max = _as_optional_finite(auto_cfg.get("driver_max_mm"))
    if driver_max is None:
        driver_max = _as_optional_finite(auto_cfg.get("span_max_mm"))
    report["driver_min_mm"] = driver_min
    report["driver_max_mm"] = driver_max

    if driver_min is None or driver_max is None or driver_max <= driver_min:
        report["reason"] = "invalid_driver_range"
        return report

    blend_factor = min(1.0, max(0.0, (driver_value - driver_min) / (driver_max - driver_min)))
    report["blend_factor"] = blend_factor

    process_mut = dict(process_cfg)
    deposition_mut = dict(process_mut.get("deposition") or {})
    path_mut = dict(process_mut.get("path") or {})

    dep_ranges = {}
    dep_cfg = auto_cfg.get("deposition")
    if isinstance(dep_cfg, Mapping) and isinstance(dep_cfg.get("ranges"), Mapping):
        dep_ranges = dep_cfg.get("ranges") or {}
    path_ranges = {}
    path_cfg = auto_cfg.get("path")
    if isinstance(path_cfg, Mapping) and isinstance(path_cfg.get("ranges"), Mapping):
        path_ranges = path_cfg.get("ranges") or {}

    dep_applied = _apply_scaled_range_values(deposition_mut, dep_ranges, blend_factor=blend_factor)
    path_applied = _apply_scaled_range_values(path_mut, path_ranges, blend_factor=blend_factor)

    report["applied_values"] = {
        "deposition": dep_applied,
        "path": path_applied,
    }
    if not dep_applied and not path_applied:
        report["reason"] = "no_matching_ranges"
        return report

    process_mut["deposition"] = deposition_mut
    process_mut["path"] = path_mut
    config["process"] = process_mut
    report["applied"] = True
    return report


def _expected_dense_layer_count(
    *,
    bbox: Mapping[str, object],
    layer_height_mm: float,
    z_offset_mm: float,
) -> int:
    try:
        zmin = float(bbox.get("zmin", 0.0))
        zmax = float(bbox.get("zmax", 0.0))
    except Exception:
        return 0
    if layer_height_mm <= 0.0:
        return 0
    start = zmin + max(0.0, z_offset_mm)
    if zmax + 1e-9 < start:
        return 0
    return int(math.floor(((zmax - start) / layer_height_mm) + 1e-9)) + 1


def _slice_skip_z_values(skips: Sequence[Mapping[str, object]]) -> List[float]:
    values: List[float] = []
    for item in skips:
        try:
            values.append(round(float(item.get("z")), 6))
        except Exception:
            continue
    return values


def _deposition_start_wcs(doc: App.Document, placement: App.Placement) -> Dict[str, object]:
    stored = get_deposition_start(doc)
    if stored is not None:
        try:
            local = placement.inverse().multVec(stored)
            return {
                "point_wcs": [float(local.x), float(local.y), float(local.z)],
                "source": "wcs_marker",
            }
        except Exception:
            pass
    return {"point_wcs": [0.0, 0.0, 0.0], "source": "wcs_origin_default"}


def _features_stub(
    *,
    part_name: str,
    wcs_name: str,
    placement: App.Placement,
    shape_wcs,
    doc: App.Document,
    wcs_source: str = "",
    wcs_warnings: Sequence[str] = (),
) -> Dict[str, object]:
    return {
        "metadata": {
            "units": "mm",
            "wcs": _wcs_metadata(wcs_name, placement, source=wcs_source, warnings=wcs_warnings),
            "work_obj": part_name,
            "bbox": _bbox_dict(shape_wcs),
            "deposition_start": _deposition_start_wcs(doc, placement),
        },
        "features": [],
    }


def _ensure_closed(points: Sequence[Point2D]) -> list[Point2D]:
    pts = [(float(x), float(y)) for x, y in points]
    if not pts:
        return []
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _loop_vertices(points: Sequence[Point2D]) -> list[Point2D]:
    pts = _ensure_closed(points)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        return pts[:-1]
    return pts


def _bbox_of_loop(points: Sequence[Point2D]) -> tuple[float, float, float, float]:
    pts = _loop_vertices(points)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _centroid(points: Sequence[Point2D]) -> Point2D:
    pts = _loop_vertices(points)
    if not pts:
        return (0.0, 0.0)
    return (sum(x for x, _y in pts) / len(pts), sum(y for _x, y in pts) / len(pts))


def _bbox_contains(bbox_xy: tuple[float, float, float, float], pt: Point2D, *, pad: float = 1e-6) -> bool:
    xmin, ymin, xmax, ymax = bbox_xy
    return (xmin - pad) <= pt[0] <= (xmax + pad) and (ymin - pad) <= pt[1] <= (ymax + pad)


def _distance_sq(a: Point2D, b: Point2D) -> float:
    dx = float(a[0]) - float(b[0])
    dy = float(a[1]) - float(b[1])
    return dx * dx + dy * dy


def _points_close(a: Point2D, b: Point2D, tol: float = 1e-6) -> bool:
    return _distance_sq(a, b) <= (tol * tol)


def _closest_point_index(points: Sequence[Point2D], target: Point2D) -> Optional[int]:
    core = _loop_vertices(points)
    if not core:
        return None
    best_idx = 0
    best_dist = None
    for idx, pt in enumerate(core):
        dist = _distance_sq(pt, target)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


def _rotate_closed_loop_to_target(points: Sequence[Point2D], target: Optional[Point2D]) -> list[Point2D]:
    loop = _ensure_closed(points)
    if target is None or len(loop) < 4:
        return loop
    core = loop[:-1]
    idx = _closest_point_index(core, target)
    if idx is None or idx <= 0:
        return loop
    rotated = core[idx:] + core[:idx]
    rotated.append(rotated[0])
    return rotated


def _rotate_point(
    x: float,
    y: float,
    *,
    angle_rad: float,
    origin: Point2D,
    inverse: bool = False,
) -> Point2D:
    if inverse:
        angle_rad = -angle_rad
    ox, oy = origin
    dx, dy = x - ox, y - oy
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return (ox + (dx * cos_a - dy * sin_a), oy + (dx * sin_a + dy * cos_a))


def _scanline_segments_at_y(
    rotated_polygon: Sequence[Point2D],
    *,
    y_value: float,
    angle_rad: float,
    origin: Point2D,
    reverse: bool = False,
) -> list[Segment2D]:
    x_hits: list[float] = []
    for (x1, y1), (x2, y2) in zip(rotated_polygon, rotated_polygon[1:]):
        if abs(y1 - y2) < 1e-9:
            continue
        if (y1 <= y_value < y2) or (y2 <= y_value < y1):
            t = (y_value - y1) / (y2 - y1)
            x_hits.append(x1 + t * (x2 - x1))
    x_hits.sort()

    line_segments: list[Segment2D] = []
    for idx in range(0, len(x_hits) - 1, 2):
        start = _rotate_point(x_hits[idx], y_value, angle_rad=angle_rad, origin=origin, inverse=True)
        end = _rotate_point(x_hits[idx + 1], y_value, angle_rad=angle_rad, origin=origin, inverse=True)
        line_segments.append((start, end))
    if reverse:
        line_segments = [(end, start) for start, end in reversed(line_segments)]
    return line_segments


def _hatch_segments_from_polygon(
    polygon: Sequence[Point2D],
    *,
    spacing: float,
    angle_deg: float,
    ensure_center_pass: bool = False,
) -> list[Segment2D]:
    pts = _ensure_closed(polygon)
    if len(pts) < 4 or spacing <= 0.0:
        return []

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    origin = ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5)
    angle_rad = math.radians(angle_deg % 180.0)

    rotated = [_rotate_point(x, y, angle_rad=angle_rad, origin=origin) for x, y in pts]
    y_vals = [p[1] for p in rotated]
    y = min(y_vals) + spacing * 0.5
    y_max = max(y_vals)

    segments: list[Segment2D] = []
    reverse = False
    while y <= y_max + 1e-6:
        segments.extend(
            _scanline_segments_at_y(
                rotated,
                y_value=y,
                angle_rad=angle_rad,
                origin=origin,
                reverse=reverse,
            )
        )
        reverse = not reverse
        y += spacing
    if not segments and ensure_center_pass:
        center_y = (min(y_vals) + max(y_vals)) * 0.5
        segments.extend(
            _scanline_segments_at_y(
                rotated,
                y_value=center_y,
                angle_rad=angle_rad,
                origin=origin,
            )
        )
    return segments


def _group_regions(contours: Sequence[Contour]) -> list[dict[str, object]]:
    outers = [contour for contour in contours if contour.is_outer]
    inners = [contour for contour in contours if not contour.is_outer]
    regions = [
        {
            "outer": contour,
            "holes": [],
            "bbox": _bbox_of_loop(contour.points),
            "centroid": _centroid(contour.points),
        }
        for contour in outers
    ]

    for hole in inners:
        hole_center = _centroid(hole.points)
        candidates = []
        for idx, region in enumerate(regions):
            bbox_xy = region["bbox"]
            outer_pts = _loop_vertices(region["outer"].points)
            if _bbox_contains(bbox_xy, hole_center) and point_in_polygon(hole_center[0], hole_center[1], outer_pts):
                area = abs(float(region["outer"].area))
                candidates.append((area, idx))
        if candidates:
            _area, region_idx = min(candidates, key=lambda item: item[0])
            regions[region_idx]["holes"].append(hole)
    return regions


def _region_strategy(
    outer_points: Sequence[Point2D],
    *,
    bead_width_mm: float,
    overlap_pct: float,
) -> str:
    xmin, ymin, xmax, ymax = _bbox_of_loop(outer_points)
    min_span = max(0.0, min(xmax - xmin, ymax - ymin))
    spacing = _path_spacing_mm(bead_width_mm, overlap_pct)
    contour_only_limit = max(bead_width_mm * 1.5, spacing * 2.0)
    if min_span <= contour_only_limit:
        return "contour_only"
    return "contour_plus_hatch"


def _path_config(config: Mapping[str, object]) -> Mapping[str, object]:
    process_cfg = config.get("process") if isinstance(config.get("process"), Mapping) else {}
    return process_cfg.get("path") if isinstance(process_cfg.get("path"), Mapping) else {}


def _float_or_default(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _deposition_pattern_from_config(config: Mapping[str, object]) -> str:
    process_cfg = config.get("process") if isinstance(config.get("process"), Mapping) else {}
    path_cfg = _path_config(config)
    raw_pattern = str(path_cfg.get("deposition_pattern") or "").strip().lower()
    if raw_pattern in {"contour_hatch", "hatch_only"}:
        return raw_pattern

    deposition_cfg = process_cfg.get("deposition") if isinstance(process_cfg.get("deposition"), Mapping) else {}
    raw_strategy = str(deposition_cfg.get("strategy") or "").strip().lower()
    if raw_strategy in {"slice_first_hatch_only", "hatch_only"}:
        return "hatch_only"
    return DEFAULT_DEPOSITION_PATTERN


def _deposition_strategy_name(deposition_pattern: str) -> str:
    if deposition_pattern == "hatch_only":
        return "slice_first_hatch_only"
    return "slice_first_contour_hatch"


def _hatch_angle_defaults(deposition_pattern: str) -> tuple[float, float]:
    if deposition_pattern == "hatch_only":
        return (0.0, 0.0)
    return (0.0, 90.0)


def _hatch_angles_from_config(config: Mapping[str, object], deposition_pattern: str) -> tuple[float, float]:
    path_cfg = _path_config(config)
    default_angle_deg, default_angle_step_deg = _hatch_angle_defaults(deposition_pattern)
    raw_angle_deg = path_cfg.get("hatch_angle_deg", path_cfg.get("angle_deg"))
    raw_angle_step_deg = path_cfg.get("hatch_angle_step_deg", path_cfg.get("angle_step_deg"))
    return (
        _float_or_default(raw_angle_deg, default_angle_deg),
        _float_or_default(raw_angle_step_deg, default_angle_step_deg),
    )


def _is_fixed_hatch_orientation(angle_step_deg: float) -> bool:
    return abs(float(angle_step_deg)) % 180.0 <= 1e-9


def _planner_basis_text(deposition_pattern: str, angle_step_deg: float) -> str:
    if deposition_pattern == "hatch_only":
        if _is_fixed_hatch_orientation(angle_step_deg):
            return "WCS XY slices with clipped longitudinal hatch-only deposition paths"
        return "WCS XY slices with clipped hatch-only deposition paths"
    return "WCS XY slices with contour and hatch deposition paths"


def _optimization_label(deposition_pattern: str, angle_step_deg: float) -> str:
    if deposition_pattern == "hatch_only":
        if _is_fixed_hatch_orientation(angle_step_deg):
            return "SLICE-FIRST LONGITUDINAL HATCH ONLY (SNAKE)"
        return "SLICE-FIRST CLIPPED HATCH"
    return "SLICE-FIRST CONTOUR + HATCH"


def _default_process_config(
    config: Mapping[str, object],
    *,
    bead_width_mm: float,
    layer_height_mm: float,
    overlap_pct: float,
    deposition_pattern: str,
    hatch_angle_deg: float,
    hatch_angle_step_deg: float,
) -> Dict[str, object]:
    process = deepcopy(dict(config.get("process") or {}))
    deposition = dict(process.get("deposition") or {})
    deposition["bead_width_mm"] = float(bead_width_mm)
    deposition["layer_height_mm"] = float(layer_height_mm)
    deposition["overlap_pct"] = float(overlap_pct)
    deposition["strategy"] = _deposition_strategy_name(deposition_pattern)
    deposition.setdefault("travel_speed_mm_min", 320.0)
    deposition.setdefault("wire_feed_mm_min", 3000.0)
    deposition.setdefault("dwell_per_layer_s", 1.5)
    deposition["support_trim"] = {"enabled": False, "radius_scale": 0.0}
    process["deposition"] = deposition

    path_cfg = dict(process.get("path") or {})
    path_cfg["planner_mode"] = "slice_first"
    path_cfg["infill_strategy"] = "hatch"
    path_cfg["deposition_pattern"] = deposition_pattern
    path_cfg["hatch_angle_deg"] = float(hatch_angle_deg)
    path_cfg["hatch_angle_step_deg"] = float(hatch_angle_step_deg)
    path_cfg["start_strategy"] = "nearest_start"
    topo_cfg = dict(path_cfg.get("topology_sensitive") or {})
    topo_cfg["enabled"] = False
    continuity_cfg = dict(topo_cfg.get("continuity") or {})
    continuity_cfg["emit_chains"] = False
    topo_cfg["continuity"] = continuity_cfg
    path_cfg["topology_sensitive"] = topo_cfg
    process["path"] = path_cfg
    return process


def _part_name(doc: App.Document, objects: Sequence[App.DocumentObject]) -> str:
    if objects:
        obj = objects[0]
        label = getattr(obj, "Label", None) or getattr(obj, "Name", None)
        if isinstance(label, str) and label.strip():
            return label.strip().replace(" ", "_")
    doc_label = getattr(doc, "Label", None) or getattr(doc, "Name", None)
    if isinstance(doc_label, str) and doc_label.strip():
        return doc_label.strip().replace(" ", "_")
    return "part"


def _append_hatch_snake_paths(
    paths: Dict[str, Dict[str, object]],
    path_ids: List[str],
    clipped_segments: Sequence[Segment2D],
    *,
    path_counter: int,
    region_id: str,
    region_mode: str,
) -> tuple[int, Optional[Point2D]]:
    last_end: Optional[Point2D] = None
    for seg_idx, (start, end) in enumerate(clipped_segments):
        if last_end is not None and not _points_close(last_end, start):
            link_id = f"P{path_counter}"
            path_counter += 1
            paths[link_id] = {
                "id": link_id,
                "role_hint": "LINK",
                "frame": "wcs_frame",
                "closed": False,
                "points_xyz": [[last_end[0], last_end[1], 0.0], [start[0], start[1], 0.0]],
                "planner_tags": {
                    "region_id": region_id,
                    "region_mode": region_mode,
                    "segment_index": seg_idx,
                    "skip_deposition": True,
                    "continuity_reversible": False,
                },
            }
            path_ids.append(link_id)

        infill_id = f"P{path_counter}"
        path_counter += 1
        paths[infill_id] = {
            "id": infill_id,
            "role_hint": "INFILL",
            "frame": "wcs_frame",
            "closed": False,
            "points_xyz": [[start[0], start[1], 0.0], [end[0], end[1], 0.0]],
            "planner_tags": {
                "region_id": region_id,
                "region_mode": region_mode,
                "segment_index": seg_idx,
                "continuity_reversible": True,
            },
        }
        path_ids.append(infill_id)
        last_end = end

    return path_counter, last_end


def _structured_toolpath(
    layers: Sequence[LayerSlice],
    *,
    bead_width_mm: float,
    overlap_pct: float,
    start_point_wcs: Optional[Sequence[float]] = None,
    angle0_deg: float = 0.0,
    angle_step_deg: float = 90.0,
    deposition_pattern: str = DEFAULT_DEPOSITION_PATTERN,
) -> Dict[str, object]:
    spacing = _path_spacing_mm(bead_width_mm, overlap_pct)
    deposition_pattern = deposition_pattern if deposition_pattern in {"contour_hatch", "hatch_only"} else DEFAULT_DEPOSITION_PATTERN
    planner_basis = _planner_basis_text(deposition_pattern, angle_step_deg)
    paths: dict[str, dict[str, object]] = {}
    layers_out: list[dict[str, object]] = []
    path_counter = 0
    layer_target: Optional[Point2D] = None
    if isinstance(start_point_wcs, Sequence) and len(start_point_wcs) >= 2:
        try:
            layer_target = (float(start_point_wcs[0]), float(start_point_wcs[1]))
        except Exception:
            layer_target = None

    for layer in layers:
        path_ids: list[str] = []
        regions = _group_regions(layer.contours)
        if layer_target is not None and regions:
            remaining = list(regions)
            ordered: list[dict[str, object]] = []
            target = layer_target
            while remaining:
                best_idx = min(
                    range(len(remaining)),
                    key=lambda idx: _distance_sq(remaining[idx]["centroid"], target),
                )
                region = remaining.pop(best_idx)
                ordered.append(region)
                target = region["centroid"]
            regions = ordered

        last_target = layer_target
        angle_deg = angle0_deg + layer.index * angle_step_deg

        for region_idx, region in enumerate(regions):
            outer = region["outer"]
            holes = list(region["holes"])
            region_id = f"L{layer.index}_R{region_idx}"
            strategy = _region_strategy(
                outer.points,
                bead_width_mm=bead_width_mm,
                overlap_pct=overlap_pct,
            )
            hatch_only_region = deposition_pattern == "hatch_only"
            clipped_segments: list[Segment2D] = []
            if hatch_only_region or strategy != "contour_only":
                hatch_segments = _hatch_segments_from_polygon(
                    _loop_vertices(outer.points),
                    spacing=spacing,
                    angle_deg=angle_deg,
                    ensure_center_pass=hatch_only_region,
                )
                clipped_segments = clip_paths_to_contours(
                    hatch_segments,
                    _loop_vertices(outer.points),
                    [_loop_vertices(hole.points) for hole in holes],
                )

            emit_contours = not hatch_only_region
            region_mode = "hatch_only" if hatch_only_region else strategy
            if emit_contours:
                outer_points = _rotate_closed_loop_to_target(outer.points, last_target)
                outer_id = f"P{path_counter}"
                path_counter += 1
                paths[outer_id] = {
                    "id": outer_id,
                    "role_hint": "OUTER",
                    "frame": "wcs_frame",
                    "closed": True,
                    "points_xyz": [[x, y, 0.0] for x, y in outer_points],
                    "planner_tags": {
                        "region_id": region_id,
                        "region_mode": region_mode,
                        "continuity_reversible": False,
                    },
                }
                path_ids.append(outer_id)
                last_target = outer_points[0]

                for hole_idx, hole in enumerate(holes):
                    hole_points = _rotate_closed_loop_to_target(hole.points, last_target)
                    hole_id = f"P{path_counter}"
                    path_counter += 1
                    paths[hole_id] = {
                        "id": hole_id,
                        "role_hint": "INNER",
                        "frame": "wcs_frame",
                        "closed": True,
                        "points_xyz": [[x, y, 0.0] for x, y in hole_points],
                        "planner_tags": {
                            "region_id": region_id,
                            "region_mode": region_mode,
                            "hole_index": hole_idx,
                            "continuity_reversible": False,
                        },
                    }
                    path_ids.append(hole_id)
                    last_target = hole_points[0]

            if not hatch_only_region and strategy == "contour_only":
                continue

            if hatch_only_region:
                path_counter, snake_end = _append_hatch_snake_paths(
                    paths,
                    path_ids,
                    clipped_segments,
                    path_counter=path_counter,
                    region_id=region_id,
                    region_mode=region_mode,
                )
                if snake_end is not None:
                    last_target = snake_end
                continue

            for seg_idx, (start, end) in enumerate(clipped_segments):
                infill_id = f"P{path_counter}"
                path_counter += 1
                paths[infill_id] = {
                    "id": infill_id,
                    "role_hint": "INFILL",
                    "frame": "wcs_frame",
                    "closed": False,
                    "points_xyz": [[start[0], start[1], 0.0], [end[0], end[1], 0.0]],
                    "planner_tags": {
                        "region_id": region_id,
                        "region_mode": region_mode,
                        "segment_index": seg_idx,
                        "continuity_reversible": True,
                    },
                }
                path_ids.append(infill_id)
                last_target = end

        layers_out.append(
            {
                "i": int(layer.index),
                "z": float(layer.z),
                "frame": "wcs_frame",
                "path_ids": path_ids,
                "planner_mode": "slice_first",
            }
        )
        if last_target is not None:
            layer_target = last_target

    return {
        "frames": {"wcs_frame": {"units": "mm"}},
        "paths": paths,
        "layers": layers_out,
        "planner_mode": "slice_first",
        "planner_basis": planner_basis,
        "deposition_pattern": deposition_pattern,
    }


def write_slice_plan_for_document(
    *,
    doc: App.Document,
    objects: Sequence[App.DocumentObject],
    config: Mapping[str, object],
    output_path: Path | str,
    config_path: Optional[Path | str] = None,
    config_override: Optional[Mapping[str, object]] = None,
    features_path: Optional[Path | str] = None,
    llm_waam_output_path: Optional[Path | str] = None,
    layer_height_mm: Optional[float] = None,
    bead_width_mm: Optional[float] = None,
    overlap_pct: Optional[float] = None,
    samples_per_edge: int = 24,
    min_contour_area: float = 1.0,
    step_path: Optional[Path | str] = None,
) -> SlicePlanArtifacts:
    if doc is None:
        raise RuntimeError("No active document is available for slice planning.")
    if not objects:
        raise ValueError("No visible solid objects were provided for slice planning.")

    merged_config = deepcopy(dict(config))
    if config_override:
        merged_config = _merge_mapping(merged_config, config_override)

    wcs_resolution = resolve_wcs(doc)
    wcs_name = str(wcs_resolution.label or merged_config.get("wcs") or "G54")
    part_name = _part_name(doc, objects)

    shape_world = build_compound_shape(objects)
    shape_wcs = transform_shape_to_local_frame(shape_world, wcs_resolution.placement)
    bbox = _bbox_dict(shape_wcs)
    auto_scale_report = _apply_auto_scale(merged_config, bbox=bbox)
    deposition_cfg = ((merged_config.get("process") or {}).get("deposition") or {}) if isinstance(merged_config, Mapping) else {}
    scaled_dep = auto_scale_report.get("applied_values") if isinstance(auto_scale_report.get("applied_values"), Mapping) else {}
    scaled_dep_values = scaled_dep.get("deposition") if isinstance(scaled_dep.get("deposition"), Mapping) else {}
    scaled_layer_height = _as_optional_finite(scaled_dep_values.get("layer_height_mm"))
    scaled_bead_width = _as_optional_finite(scaled_dep_values.get("bead_width_mm"))
    scaled_overlap = _as_optional_finite(scaled_dep_values.get("overlap_pct"))
    layer_height = float(
        scaled_layer_height
        if scaled_layer_height is not None
        else (layer_height_mm or deposition_cfg.get("layer_height_mm") or DEFAULT_LAYER_HEIGHT_MM)
    )
    bead_width = float(
        scaled_bead_width
        if scaled_bead_width is not None
        else (bead_width_mm or deposition_cfg.get("bead_width_mm") or DEFAULT_BEAD_WIDTH_MM)
    )
    overlap = float(
        scaled_overlap
        if scaled_overlap is not None
        else (overlap_pct if overlap_pct is not None else deposition_cfg.get("overlap_pct") or DEFAULT_OVERLAP_PCT)
    )
    deposition_pattern = _deposition_pattern_from_config(merged_config)
    hatch_angle_deg, hatch_angle_step_deg = _hatch_angles_from_config(merged_config, deposition_pattern)
    bbox_dims = _bbox_dimensions(bbox)
    interline_spacing = _path_spacing_mm(bead_width, overlap)
    slice_offset = max(0.0, layer_height * 0.5)
    slice_skip_diagnostics: List[Dict[str, object]] = []
    layers = generate_layer_slices(
        shape_wcs,
        layer_height=layer_height,
        samples_per_edge=samples_per_edge,
        z_offset=slice_offset,
        min_contour_area=min_contour_area,
        max_layers=None,
        stitch_close_tol=0.5,
        diagnostics=slice_skip_diagnostics,
    )
    if not layers:
        raise RuntimeError("Slicing produced no usable layers.")
    expected_layers = _expected_dense_layer_count(
        bbox=bbox,
        layer_height_mm=layer_height,
        z_offset_mm=slice_offset,
    )
    if expected_layers >= 8 and len(layers) < max(4, int(math.ceil(expected_layers * 0.4))):
        raise RuntimeError(
            "Slicing produced too few populated layers for the part height. "
            f"Generated {len(layers)} populated layers, but the WCS bbox height "
            f"({bbox['zmax'] - bbox['zmin']:.3f} mm) suggests roughly {expected_layers} "
            f"layers at {layer_height:.3f} mm. "
            "This usually means the section extraction failed or the WCS build axis is wrong."
        )
    if slice_skip_diagnostics:
        try:
            App.Console.PrintWarning(
                "WAAM: Slicing skipped "
                f"{len(slice_skip_diagnostics)} candidate Z plane(s); "
                "see metadata.slice_diagnostics.skipped_z_details in the slice plan.\n"
            )
        except Exception:
            pass

    start_meta = _deposition_start_wcs(doc, wcs_resolution.placement)
    deposition_start_source = str(start_meta.get("source") or "")
    structured = _structured_toolpath(
        layers,
        bead_width_mm=bead_width,
        overlap_pct=overlap,
        start_point_wcs=start_meta.get("point_wcs"),
        angle0_deg=hatch_angle_deg,
        angle_step_deg=hatch_angle_step_deg,
        deposition_pattern=deposition_pattern,
    )

    features_payload = _features_stub(
        part_name=part_name,
        wcs_name=wcs_name,
        placement=wcs_resolution.placement,
        shape_wcs=shape_wcs,
        doc=doc,
        wcs_source=wcs_resolution.source,
        wcs_warnings=wcs_resolution.warnings,
    )
    metadata = dict(features_payload["metadata"])
    metadata["program"] = "WAAM_BASELINE.NC"
    metadata["optimizations"] = _optimization_label(deposition_pattern, hatch_angle_step_deg)
    metadata["slice_diagnostics"] = {
        "bbox_width_mm": float(bbox_dims["width_mm"]),
        "bbox_depth_mm": float(bbox_dims["depth_mm"]),
        "bbox_height_mm": float(bbox["zmax"] - bbox["zmin"]),
        "bbox_max_span_mm": float(bbox_dims["max_span_mm"]),
        "expected_dense_layer_count": int(expected_layers),
        "generated_layer_count": int(len(layers)),
        "skipped_z_count": int(len(slice_skip_diagnostics)),
        "skipped_z_values": _slice_skip_z_values(slice_skip_diagnostics),
        "skipped_z_details": deepcopy(slice_skip_diagnostics),
        "slice_offset_mm": float(slice_offset),
        "interline_spacing_mm": float(interline_spacing),
    }

    config_hash = short_hash(sha256_json(merged_config))
    step_hash: Optional[str] = None
    step_path_str: Optional[str] = None
    if step_path:
        step_path_obj = Path(step_path).expanduser()
        step_path_str = str(step_path_obj)
        if step_path_obj.is_file():
            step_hash = short_hash(sha256_file(step_path_obj))

    stage_manifest = {
        "stage": "slice",
        "generator_version": SLICE_PLAN_GENERATOR_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "model": {
            "step_path": step_path_str,
            "step_sha256_16": step_hash,
            "doc_label": getattr(doc, "Label", None) or getattr(doc, "Name", None),
        },
        "wcs": {
            "name": wcs_name,
            "source": wcs_resolution.source,
            "warnings": list(wcs_resolution.warnings),
        },
        "auto_scale": auto_scale_report,
        "config_sha256_16": config_hash,
        "layer_count": int(len(layers)),
        "path_count": int(len(structured.get("paths") or {})),
        "deposition_start": dict(start_meta),
    }
    metadata["stage_manifest"] = stage_manifest

    process_cfg = _default_process_config(
        merged_config,
        bead_width_mm=bead_width,
        layer_height_mm=layer_height,
        overlap_pct=overlap,
        deposition_pattern=deposition_pattern,
        hatch_angle_deg=hatch_angle_deg,
        hatch_angle_step_deg=hatch_angle_step_deg,
    )
    post_cfg = merged_config.get("post") or (merged_config.get("machine") or {}).get("post") or {}

    plan_payload = {
        "slice_plan_version": 1,
        "planner_mode": "slice_first",
        "deposition_pattern": deposition_pattern,
        "metadata": metadata,
        "process": process_cfg,
        "process_parameters": deepcopy(process_cfg),
        "post": post_cfg,
        "llm_context": {
            "version": 1,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "waam_structured": structured,
            "summary": {
                "frames": structured.get("frames"),
                "planner_mode": "slice_first",
                "deposition_pattern": deposition_pattern,
                "layer_count": len(layers),
                "path_count": len(structured.get("paths") or {}),
            },
            "sources": {
                "config_path": str(Path(config_path).expanduser().resolve()) if config_path else "",
            },
        },
        "sources": {
            "config_path": str(Path(config_path).expanduser().resolve()) if config_path else "",
            "features_path": str(Path(features_path).expanduser().resolve()) if features_path else "",
            "llm_waam_path": str(Path(llm_waam_output_path).expanduser().resolve()) if llm_waam_output_path else "",
        },
    }

    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan_payload, indent=2), encoding="utf-8")

    manifest_target = output.with_name(output.stem + ".manifest.json")
    manifest_payload = dict(stage_manifest)
    manifest_payload["plan_path"] = str(output)
    manifest_payload["plan_sha256_16"] = short_hash(sha256_file(output))
    manifest_target.write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")

    features_target = None
    if features_path:
        features_target = Path(features_path).expanduser().resolve()
        features_target.parent.mkdir(parents=True, exist_ok=True)
        features_target.write_text(json.dumps(features_payload, indent=2), encoding="utf-8")

    llm_target = None
    if llm_waam_output_path:
        llm_target = Path(llm_waam_output_path).expanduser().resolve()
        write_llm_waam_dsl(
            llm_target,
            part_name=part_name,
            layers=list(layers),
            units="mm",
            layer_height=layer_height,
            bead_width=bead_width,
        )

    return SlicePlanArtifacts(
        plan_path=output,
        features_path=features_target,
        llm_waam_path=llm_target,
        layer_count=len(layers),
        path_count=len(structured.get("paths") or {}),
        wcs_source=wcs_resolution.source,
        wcs_warnings=tuple(wcs_resolution.warnings),
        deposition_start_source=deposition_start_source,
        manifest_path=manifest_target,
    )


__all__ = [
    "DEFAULT_BEAD_WIDTH_MM",
    "DEFAULT_DEPOSITION_PATTERN",
    "DEFAULT_LAYER_HEIGHT_MM",
    "DEFAULT_OVERLAP_PCT",
    "SlicePlanArtifacts",
    "build_compound_shape",
    "write_slice_plan_for_document",
]
