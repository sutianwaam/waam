from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..core.config_loader import load_standard_config
from .spiral_stc import spiral_coverage_from_polygon
from .waam_config import normalize_waam_config, validate_features

Point2D = Tuple[float, float]
Point3DS = Tuple[float, float, float, float]

_LAYER_ASSIGNMENT_MODE = "nearest_representative_z"
_AREA_ESTIMATE_REPORTED = "reported"
_AREA_ESTIMATE_BBOX = "bbox"
_AREA_ESTIMATE_UNKNOWN = "unknown"
_GEOMETRY_SOURCE_EXPLICIT = "explicit_loops"
_GEOMETRY_SOURCE_BBOX = "bbox_fallback"
_GEOMETRY_CONFIDENCE_HIGH = "high"
_GEOMETRY_CONFIDENCE_DEGRADED = "degraded"
_HOLE_KEEPOUT_RADIUS_SCALE = 1.1
_MIN_PATH_SPACING_MM = 0.2
_MIN_OVERLAP_FACTOR = 0.05

_RISK_WEIGHTS = {
    "overhang_region": 3,
    "cantilever": 3,
    "support_region": 2,
    "support": 2,
    "overhang_neighbor": 2,
    "thin_wall_neighbor": 2,
    "thin_wall": 2,
}

_HATCH_TYPE_CODE = {
    "contour": 10,
    "zigzag": 11,
    "raster": 11,
    "line": 11,
    "spiral_stc": 12,
    "contour_parallel": 13,
    "support": 20,
    "cantilever": 21,
}

_NON_DEPOSITION_FEATURE_TYPES = {
    "through_hole",
    "cylindrical_hole",
    # freeform_area currently falls back to bbox-only geometry when robust loops
    # are unavailable; allowing deposition here can bridge intended voids.
    "freeform_area",
}


# -----------------------------
# Generic helpers
# -----------------------------

def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _as_lower_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _resolve_planner_config(config: Any) -> Any:
    if isinstance(config, Path):
        return str(config)
    if config is not None:
        return config
    try:
        return load_standard_config()
    except Exception:
        return {}


def _build_tool_index(tools: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for tool in tools:
        name = tool.get("name")
        ttype = tool.get("type")
        if name and name not in by_key:
            by_key[str(name)] = tool
        if ttype:
            key = f"type::{ttype}"
            if key not in by_key:
                by_key[key] = tool
    return by_key


def _pick_tool(tool_index: Mapping[str, Dict[str, Any]], *candidates: str) -> Optional[str]:
    for candidate in candidates:
        tool = tool_index.get(candidate)
        if tool and tool.get("name"):
            return str(tool["name"])
    return None


def _distance_3d(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(
        (float(b[0]) - float(a[0])) ** 2 +
        (float(b[1]) - float(a[1])) ** 2 +
        (float(b[2]) - float(a[2])) ** 2
    )


def _cumulative_points(points_xyz: Sequence[Tuple[float, float, float]]) -> List[Point3DS]:
    total = 0.0
    out: List[Point3DS] = []
    prev = None
    for p in points_xyz:
        if prev is not None:
            total += _distance_3d(prev, p)
        out.append((round(float(p[0]), 6), round(float(p[1]), 6), round(float(p[2]), 6), round(total, 6)))
        prev = p
    return out


def _rotate_point(point: Point2D, angle_deg: float, origin: Point2D) -> Point2D:
    theta = math.radians(angle_deg)
    ct = math.cos(theta)
    st = math.sin(theta)
    px, py = point
    ox, oy = origin
    dx = px - ox
    dy = py - oy
    return (ox + dx * ct - dy * st, oy + dx * st + dy * ct)


# -----------------------------
# Feature summarization / layers
# -----------------------------

def _feature_bbox_xyz(feature: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    bbox = feature.get("bbox")
    if isinstance(bbox, Mapping):
        xmin = _as_float(bbox.get("xmin"))
        xmax = _as_float(bbox.get("xmax"))
        ymin = _as_float(bbox.get("ymin"))
        ymax = _as_float(bbox.get("ymax"))
        zmin = _as_float(bbox.get("zmin"), 0.0)
        zmax = _as_float(bbox.get("zmax"), zmin)
        if None not in (xmin, xmax, ymin, ymax):
            return {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax, "zmin": zmin or 0.0, "zmax": zmax or 0.0}
    elif isinstance(bbox, (list, tuple)):
        vals = [_as_float(v) for v in bbox]
        if len(vals) >= 4 and None not in vals[:4]:
            zmin = vals[4] if len(vals) > 4 and vals[4] is not None else _as_float(feature.get("z"), 0.0) or 0.0
            zmax = vals[5] if len(vals) > 5 and vals[5] is not None else zmin
            return {"xmin": vals[0], "xmax": vals[1], "ymin": vals[2], "ymax": vals[3], "zmin": zmin, "zmax": zmax}
    return None


def _feature_layer_info(feature: Mapping[str, Any], layer_height: float) -> Dict[str, Any]:
    z_candidates = [
        _as_float(feature.get("z")),
        _as_float(feature.get("z_top")),
        _as_float(feature.get("z_top_mm")),
    ]
    bbox = _feature_bbox_xyz(feature)
    if bbox:
        z_candidates.append(_as_float(bbox.get("zmin")))
        z_candidates.append(_as_float(bbox.get("zmax")))
    center = feature.get("center")
    if isinstance(center, (list, tuple)) and len(center) >= 3:
        z_candidates.append(_as_float(center[2]))

    z_mm = next((z for z in z_candidates if z is not None), 0.0)
    h = layer_height if layer_height and layer_height > 0 else 1.0
    layer_index = int(round(z_mm / h))
    z_min = _as_float((bbox or {}).get("zmin"), z_mm) if bbox else z_mm
    z_max = _as_float((bbox or {}).get("zmax"), z_mm) if bbox else z_mm
    return {
        "index": layer_index,
        "z_mm": float(z_mm),
        "representative_z_mm": float(z_mm),
        "z_min_mm": float(z_min if z_min is not None else z_mm),
        "z_max_mm": float(z_max if z_max is not None else z_mm),
        "layer_assignment_mode": _LAYER_ASSIGNMENT_MODE,
    }


def _feature_area_info(feature: Mapping[str, Any]) -> Dict[str, Any]:
    area = _as_float(feature.get("area_mm2"))
    if area is not None and area > 0:
        return {
            "area_mm2": float(area),
            "area_mm2_estimated": float(area),
            "area_estimate_method": _AREA_ESTIMATE_REPORTED,
        }
    bbox = _feature_bbox_xyz(feature)
    if bbox:
        bbox_area = max(0.0, (bbox["xmax"] - bbox["xmin"]) * (bbox["ymax"] - bbox["ymin"]))
        return {
            "area_mm2": None,
            "area_mm2_estimated": float(bbox_area),
            "area_estimate_method": _AREA_ESTIMATE_BBOX,
        }
    return {
        "area_mm2": None,
        "area_mm2_estimated": 0.0,
        "area_estimate_method": _AREA_ESTIMATE_UNKNOWN,
    }


def _feature_geometry_provenance(feature: Mapping[str, Any]) -> Dict[str, Any]:
    outer = _coerce_loop(feature.get("outer_loop"))
    inner_raw = feature.get("inner_loops") or []
    inner_count = 0
    if isinstance(inner_raw, (list, tuple)):
        for loop in inner_raw:
            co = _coerce_loop(loop)
            if co and len(co) >= 3:
                inner_count += 1
    if outer and len(outer) >= 3:
        return {
            "geometry_source": _GEOMETRY_SOURCE_EXPLICIT,
            "geometry_confidence": _GEOMETRY_CONFIDENCE_HIGH,
            "bbox_fallback_used": False,
            "has_outer_loop": True,
            "inner_loop_count": inner_count,
        }
    bbox = _feature_bbox_xyz(feature)
    if bbox:
        return {
            "geometry_source": _GEOMETRY_SOURCE_BBOX,
            "geometry_confidence": _GEOMETRY_CONFIDENCE_DEGRADED,
            "bbox_fallback_used": True,
            "has_outer_loop": False,
            "inner_loop_count": 0,
        }
    return {
        "geometry_source": "missing_geometry",
        "geometry_confidence": _GEOMETRY_CONFIDENCE_DEGRADED,
        "bbox_fallback_used": False,
        "has_outer_loop": False,
        "inner_loop_count": 0,
    }


def _summarize_feature(feature: Mapping[str, Any], layer_height: float) -> Dict[str, Any]:
    summary = {
        "id": feature.get("id"),
        "type": feature.get("type"),
        "owner": feature.get("owner"),
        "risk_tags": feature.get("risk_tags") or [],
        "layer": _feature_layer_info(feature, layer_height),
        "planning_provenance": _feature_geometry_provenance(feature),
    }
    summary.update(_feature_area_info(feature))
    for key in (
        "center",
        "axis_wcs",
        "bbox",
        "diameter",
        "depth",
        "area_mm2",
        "z",
        "z_top",
        "vertical_like",
        "outer_loop",
        "inner_loops",
        "min_radius_mm",
    ):
        value = feature.get(key)
        if value not in (None, [], {}):
            summary[key] = value
    return summary


def _feature_footprint_area(feature: Mapping[str, Any]) -> float:
    estimated = _as_float(feature.get("area_mm2_estimated"))
    if estimated is not None and estimated > 0:
        return estimated
    area = _as_float(feature.get("area_mm2"))
    if area is not None and area > 0:
        return area
    bbox = _feature_bbox_xyz(feature)
    if not bbox:
        return 0.0
    return max(0.0, (bbox["xmax"] - bbox["xmin"]) * (bbox["ymax"] - bbox["ymin"]))


def _risk_score(tags: Iterable[str] | None) -> int:
    score = 0
    for tag in tags or []:
        score += _RISK_WEIGHTS.get(str(tag), 1)
    return score


def _reorder_sequence_within_layers(sequence: List[Dict[str, Any]], feature_index: Mapping[str, Mapping[str, Any]], strategy: str = "risk_area") -> List[Dict[str, Any]]:
    normalized = (strategy or "").strip().lower()
    if normalized in {"", "none", "off", "disabled"}:
        return sequence

    grouped: Dict[int, List[Tuple[int, float, int, Dict[str, Any]]]] = {}
    leftovers: List[Dict[str, Any]] = []

    for entry in sequence:
        layer_idx = entry.get("layer_index")
        feat_id = entry.get("feature_id")
        if layer_idx is None:
            leftovers.append(entry)
            continue
        summary = feature_index.get(str(feat_id)) or {}
        risk = _risk_score(summary.get("risk_tags"))
        area = _feature_footprint_area(summary)
        pri = 0 if entry.get("op") == "deposit_contour" else 1
        grouped.setdefault(int(layer_idx), []).append((risk, area, pri, entry))

    out: List[Dict[str, Any]] = []
    for layer_idx in sorted(grouped):
        items = grouped[layer_idx]
        reverse_risk = normalized in {"risk_area_desc", "risk_desc"}
        items.sort(key=lambda item: ((-item[0] if reverse_risk else item[0]), -item[1], item[2]))
        out.extend(item[-1] for item in items)
    out.extend(leftovers)
    return out


# -----------------------------
# Geometry helpers
# -----------------------------

def _coerce_point2d(raw: Any) -> Optional[Point2D]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        x = _as_float(raw[0])
        y = _as_float(raw[1])
        if x is not None and y is not None:
            return (float(x), float(y))
    return None


def _coerce_loop(raw: Any) -> Optional[List[Point2D]]:
    if not isinstance(raw, (list, tuple)):
        return None
    pts = []
    for item in raw:
        pt = _coerce_point2d(item)
        if pt is not None:
            pts.append(pt)
    if len(pts) < 2:
        return None
    return pts


def _normalize_closed_loop(loop: Sequence[Point2D]) -> List[Point2D]:
    out = [(float(x), float(y)) for x, y in loop]
    if len(out) >= 2 and out[0] != out[-1]:
        out.append(out[0])
    return out


def _extract_region_geometry(feature: Mapping[str, Any]) -> Dict[str, Any]:
    outer = _coerce_loop(feature.get("outer_loop"))
    inner_raw = feature.get("inner_loops") or []
    inners = []
    if isinstance(inner_raw, (list, tuple)):
        for loop in inner_raw:
            co = _coerce_loop(loop)
            if co and len(co) >= 3:
                inners.append(_normalize_closed_loop(co))

    if outer and len(outer) >= 3:
        outer = _normalize_closed_loop(outer)
        return {"outer": outer, "inners": inners}

    bbox = _feature_bbox_xyz(feature)
    if bbox:
        poly = [
            (bbox["xmin"], bbox["ymin"]),
            (bbox["xmax"], bbox["ymin"]),
            (bbox["xmax"], bbox["ymax"]),
            (bbox["xmin"], bbox["ymax"]),
            (bbox["xmin"], bbox["ymin"]),
        ]
        return {"outer": poly, "inners": []}

    return {"outer": None, "inners": []}


def _polygon_center(loop: Sequence[Point2D]) -> Point2D:
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    return (0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys)))


def _point_in_polygon(pt: Point2D, loop: Sequence[Point2D]) -> bool:
    x, y = pt
    inside = False
    n = len(loop)
    if n < 3:
        return False
    for i in range(n - 1):
        (x1, y1), (x2, y2) = loop[i], loop[i + 1]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / ((y2 - y1) if (y2 - y1) else 1e-12)
            if x < x_cross:
                inside = not inside
    return inside


def _circle_loop(center: Point2D, radius: float, segments: int = 36) -> List[Point2D]:
    cx, cy = center
    r = max(0.05, float(radius))
    pts: List[Point2D] = []
    for i in range(max(12, int(segments))):
        a = 2.0 * math.pi * i / float(max(12, int(segments)))
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append(pts[0])
    return pts


def _intersections_with_horizontal(loop: Sequence[Point2D], y: float) -> List[float]:
    xs: List[float] = []
    for i in range(len(loop) - 1):
        (x1, y1), (x2, y2) = loop[i], loop[i + 1]
        if y1 == y2:
            continue
        if not ((y1 <= y < y2) or (y2 <= y < y1)):
            continue
        t = (y - y1) / (y2 - y1)
        xs.append(float(x1 + t * (x2 - x1)))
    xs.sort()
    return xs


def _intervals_from_loop(loop: Sequence[Point2D], y: float) -> List[Tuple[float, float]]:
    xs = _intersections_with_horizontal(loop, y)
    intervals = []
    for i in range(0, len(xs) - 1, 2):
        x1 = xs[i]
        x2 = xs[i + 1]
        if x2 > x1:
            intervals.append((x1, x2))
    return intervals


def _difference_intervals(base: List[Tuple[float, float]], holes: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    result = list(base)
    for hx1, hx2 in holes:
        next_result: List[Tuple[float, float]] = []
        for bx1, bx2 in result:
            if hx2 <= bx1 or hx1 >= bx2:
                next_result.append((bx1, bx2))
                continue
            if hx1 > bx1:
                next_result.append((bx1, min(hx1, bx2)))
            if hx2 < bx2:
                next_result.append((max(hx2, bx1), bx2))
        result = [(a, b) for a, b in next_result if b - a > 1e-6]
    return result


def _raster_fill(outer: Sequence[Point2D], inners: Sequence[Sequence[Point2D]], spacing: float, angle_deg: float, alternate_direction: bool = True) -> List[List[Point2D]]:
    origin = _polygon_center(outer)
    outer_r = [_rotate_point(p, -angle_deg, origin) for p in outer]
    holes_r = [[_rotate_point(p, -angle_deg, origin) for p in loop] for loop in inners]

    ys = [p[1] for p in outer_r]
    ymin, ymax = min(ys), max(ys)
    if spacing <= 0:
        spacing = max(0.25, (ymax - ymin) / 6.0)

    tracks: List[List[Point2D]] = []
    row = 0
    y = ymin + 0.5 * spacing
    while y <= ymax - 0.5 * spacing + 1e-9:
        base = _intervals_from_loop(outer_r, y)
        holes: List[Tuple[float, float]] = []
        for hole in holes_r:
            holes.extend(_intervals_from_loop(hole, y))
        segments = _difference_intervals(base, holes)
        for x1, x2 in segments:
            seg = [(x1, y), (x2, y)]
            if alternate_direction and row % 2 == 1:
                seg.reverse()
            tracks.append([_rotate_point(p, angle_deg, origin) for p in seg])
        row += 1
        y += spacing
    return tracks


def _spiral_fill(outer: Sequence[Point2D], spacing: float) -> List[List[Point2D]]:
    if not outer:
        return []
    loop = list(outer[:-1]) if len(outer) >= 2 and outer[0] == outer[-1] else list(outer)
    if len(loop) < 3:
        return []
    points = spiral_coverage_from_polygon(loop, spacing=max(0.2, float(spacing)))
    if len(points) < 2:
        return []
    return [[(float(x), float(y)) for x, y in points]]


def _infill_tracks(
    *,
    outer: Sequence[Point2D],
    inners: Sequence[Sequence[Point2D]],
    spacing: float,
    angle_deg: float,
    alternate_direction: bool,
    requested_strategy: str,
) -> Tuple[List[List[Point2D]], str, Optional[str]]:
    strategy = _as_lower_str(requested_strategy) or "zigzag"
    if strategy == "spiral_stc" and not inners:
        spiral_tracks = _spiral_fill(outer, spacing)
        if spiral_tracks:
            return spiral_tracks, "spiral_stc", None
    raster_tracks = _raster_fill(outer, inners, spacing=spacing, angle_deg=angle_deg, alternate_direction=alternate_direction)
    if strategy == "spiral_stc" and inners:
        return raster_tracks, "zigzag", "spiral_stc_disabled_by_inner_loops"
    if strategy == "raster":
        return raster_tracks, "raster", None
    if strategy == "line":
        return raster_tracks, "line", None
    return raster_tracks, "zigzag", None


def _contour_paths(outer: Sequence[Point2D], inners: Sequence[Sequence[Point2D]], contour_passes: int = 1) -> List[List[Point2D]]:
    paths: List[List[Point2D]] = []
    if contour_passes <= 0:
        return paths
    outer_closed = _normalize_closed_loop(outer)
    inner_closed = [_normalize_closed_loop(loop) for loop in inners]
    for _ in range(int(contour_passes)):
        paths.append(outer_closed[:])
        for hole in inner_closed:
            paths.append(hole[:])
    return paths


# -----------------------------
# WAAM planning
# -----------------------------

def _op_name_for_feature(feature: Mapping[str, Any]) -> str:
    tags = {_as_lower_str(tag) for tag in (feature.get("risk_tags") or [])}
    ftype = _as_lower_str(feature.get("type"))
    if any(tag in tags for tag in ("overhang_region", "cantilever")) or "cantilever" in ftype or "overhang" in ftype:
        return "deposit_cantilever"
    if any(tag in tags for tag in ("support_region", "support")) or "support" in ftype:
        return "deposit_support"
    return "deposit_infill"


def _process_profile_for_operation(op_name: str) -> str:
    if op_name == "deposit_contour":
        return "deposition_contour"
    if op_name == "deposit_support":
        return "support"
    if op_name == "deposit_cantilever":
        return "cantilever"
    if op_name == "interpass_cooling":
        return "interpass"
    return "deposition"


def _feature_span(feature: Mapping[str, Any]) -> float:
    bbox = _feature_bbox_xyz(feature)
    if not bbox:
        return 0.0
    return max(bbox["xmax"] - bbox["xmin"], bbox["ymax"] - bbox["ymin"])


def _filter_small_planar_faces(features: Iterable[Mapping[str, Any]], min_span_mm: float) -> Tuple[List[Dict[str, Any]], List[str]]:
    kept: List[Dict[str, Any]] = []
    dropped: List[str] = []
    for feat in features:
        feat = dict(feat)
        if feat.get("type") == "planar_pocket_floor" and min_span_mm > 0 and _feature_span(feat) < min_span_mm:
            if feat.get("id"):
                dropped.append(str(feat["id"]))
            continue
        kept.append(feat)
    return kept, dropped


def _build_sequence_entry(
    *,
    op: str,
    tool: Optional[str],
    feature: Mapping[str, Any],
    layer_index: int,
    layer_z_mm: float,
    points2d: Sequence[Point2D],
    strategy: str,
    cfg: Mapping[str, Any],
    bead_index: int,
    planning_provenance: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    dep = (cfg.get("process") or {}).get("deposition") or {}
    path = (cfg.get("process") or {}).get("path") or {}
    post = cfg.get("post") or {}

    pts3 = [(float(x), float(y), float(layer_z_mm)) for x, y in points2d]
    cum = _cumulative_points(pts3)
    length_mm = cum[-1][3] if cum else 0.0
    travel_speed = _as_float(dep.get("travel_speed_mm_min"), 0.0) or 0.0
    wire_feed = _as_float(dep.get("wire_feed_mm_min"), 0.0) or 0.0
    dwell = _as_float(dep.get("dwell_per_layer_s"), 0.0) or 0.0

    return {
        "op": op,
        "tool": tool,
        "region": feature.get("id"),
        "feature_id": feature.get("id"),
        "feature_type": feature.get("type"),
        "feature_tags": feature.get("risk_tags") or [],
        "layer_index": layer_index,
        "layer_z_mm": float(layer_z_mm),
        "process_profile": _process_profile_for_operation(op),
        "bead_index": bead_index,
        "trajectory": {
            "points": cum,
            "length_mm": length_mm,
        },
        "params": {
            "strategy": strategy,
            "strategy_code": _HATCH_TYPE_CODE.get(strategy, 0),
            "travel_speed_mm_min": travel_speed,
            "wire_feed_mm_min": wire_feed,
            "arc_current_a": dep.get("arc_current_a"),
            "arc_voltage_v": dep.get("arc_voltage_v"),
            "bead_width_mm": dep.get("bead_width_mm"),
            "layer_height_mm": dep.get("layer_height_mm"),
            "overlap_pct": dep.get("overlap_pct"),
            "ramp_in_mm": dep.get("ramp_in_mm"),
            "ramp_out_mm": dep.get("ramp_out_mm"),
            "lead_in_type": path.get("lead_in_type"),
            "lead_in_radius_mm": path.get("lead_in_radius_mm"),
            "lead_out_type": path.get("lead_out_type"),
            "lead_out_radius_mm": path.get("lead_out_radius_mm"),
            "interpass_dwell_s": dwell,
            "post": {
                "torch_on": post.get("torch_on"),
                "torch_off": post.get("torch_off"),
                "gas_on": post.get("gas_on"),
                "gas_off": post.get("gas_off"),
                "wire_feed_cmd": post.get("wire_feed"),
                "travel_speed_cmd": post.get("travel_speed"),
            },
        },
        "planning_provenance": dict(planning_provenance or {}),
    }


def _build_feature_ops(
    feature: Mapping[str, Any],
    cfg: Mapping[str, Any],
    tool_name: Optional[str],
    bead_start_index: int = 1,
    hole_keepouts: Sequence[Mapping[str, Any]] = (),
) -> Tuple[List[Dict[str, Any]], int]:
    process = cfg.get("process") or {}
    dep = process.get("deposition") or {}
    path = process.get("path") or {}
    layer_height = _as_float(dep.get("layer_height_mm"), 1.0) or 1.0
    line_spacing = _as_float(dep.get("bead_width_mm"), 6.0) or 6.0
    overlap_pct = _as_float(dep.get("overlap_pct"), 30.0) or 30.0
    spacing = max(_MIN_PATH_SPACING_MM, line_spacing * max(_MIN_OVERLAP_FACTOR, 1.0 - overlap_pct / 100.0))
    layer = _feature_layer_info(feature, layer_height)
    layer_index = int(layer["index"])
    layer_z_mm = float(layer["z_mm"])

    geom = _extract_region_geometry(feature)
    outer = geom["outer"]
    inners = geom["inners"]
    feature_provenance = dict(_feature_geometry_provenance(feature))
    feature_provenance.update(
        {
            "requested_strategy": None,
            "resolved_strategy": None,
            "resolution_reason": None,
            "hole_keepouts_applied": False,
            "hole_keepout_count": 0,
            "hole_keepout_method": None,
        }
    )
    if not outer:
        feature_provenance["resolution_reason"] = "missing_region_geometry"
        return [], bead_start_index

    # Add explicit hole keepouts when feature extraction did not produce inner loops.
    # This prevents deposition across known through-holes.
    keepout_count = 0
    for hole in hole_keepouts:
        c = hole.get("center_xy")
        r = hole.get("radius")
        if not c or r is None:
            continue
        cxy = (float(c[0]), float(c[1]))
        if _point_in_polygon(cxy, outer):
            inners.append(_circle_loop(cxy, float(r), segments=40))
            keepout_count += 1
    if keepout_count:
        feature_provenance["hole_keepouts_applied"] = True
        feature_provenance["hole_keepout_count"] = keepout_count
        feature_provenance["hole_keepout_method"] = "derived_from_detected_holes"

    alternate = _as_bool(path.get("alternate_direction"), True)
    angle0 = _as_float(path.get("angle_deg"), 0.0) or 0.0
    angle_step = _as_float(path.get("angle_step_deg"), 90.0) or 0.0
    angle_deg = angle0 + layer_index * angle_step
    contour_passes = int(path.get("contour_passes", 1) or 1)
    contours_first = _as_bool(path.get("contours_first"), True)
    deposition_strategy = _as_lower_str(dep.get("strategy") or "contour_parallel") or "contour_parallel"
    requested_infill_strategy = str(path.get("infill_strategy") or deposition_strategy or "zigzag")
    feature_provenance["requested_strategy"] = requested_infill_strategy

    infill_op = _op_name_for_feature(feature)
    sequence: List[Dict[str, Any]] = []
    bead_idx = bead_start_index

    contour_entries = []
    if deposition_strategy in {"contour_parallel", "contour_then_infill", "contour"}:
        for contour in _contour_paths(outer, inners, contour_passes=contour_passes):
            contour_entries.append(
                _build_sequence_entry(
                    op="deposit_contour",
                    tool=tool_name,
                    feature=feature,
                    layer_index=layer_index,
                    layer_z_mm=layer_z_mm,
                    points2d=contour,
                    strategy="contour",
                    cfg=cfg,
                    bead_index=bead_idx,
                    planning_provenance={
                        **feature_provenance,
                        "resolved_strategy": "contour",
                        "resolution_reason": feature_provenance.get("resolution_reason") or "contour_pass",
                    },
                )
            )
            bead_idx += 1

    infill_entries = []
    tracks, infill_strategy, infill_resolution_reason = _infill_tracks(
        outer=outer,
        inners=inners,
        spacing=spacing,
        angle_deg=angle_deg,
        alternate_direction=alternate,
        requested_strategy=requested_infill_strategy,
    )
    feature_provenance["resolved_strategy"] = infill_strategy
    if infill_resolution_reason:
        feature_provenance["resolution_reason"] = infill_resolution_reason
    elif feature_provenance.get("resolution_reason") is None:
        feature_provenance["resolution_reason"] = "requested_strategy_applied"
    for track in tracks:
        infill_entries.append(
            _build_sequence_entry(
                op=infill_op,
                tool=tool_name,
                feature=feature,
                layer_index=layer_index,
                layer_z_mm=layer_z_mm,
                points2d=track,
                strategy=infill_strategy,
                cfg=cfg,
                bead_index=bead_idx,
                planning_provenance=feature_provenance,
            )
        )
        bead_idx += 1

    if contours_first:
        sequence.extend(contour_entries)
        sequence.extend(infill_entries)
    else:
        sequence.extend(infill_entries)
        sequence.extend(contour_entries)

    return sequence, bead_idx


def _is_deposition_feature(feature: Mapping[str, Any]) -> bool:
    ftype = _as_lower_str(feature.get("type"))
    if ftype in _NON_DEPOSITION_FEATURE_TYPES:
        return False
    return True


def _build_layers_from_sequence(sequence: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[int, Dict[str, Any]] = {}
    for idx, entry in enumerate(sequence):
        layer_idx = entry.get("layer_index")
        if layer_idx is None:
            continue
        bucket = buckets.setdefault(
            int(layer_idx),
            {"index": int(layer_idx), "z_mm": entry.get("layer_z_mm", 0.0), "features": [], "operations": [], "beads": []},
        )
        feat_id = entry.get("feature_id")
        if feat_id and feat_id not in bucket["features"]:
            bucket["features"].append(feat_id)
        bucket["operations"].append({"op": entry.get("op"), "sequence_index": idx, "feature_id": feat_id})
        traj = entry.get("trajectory") or {}
        if traj.get("points"):
            bucket["beads"].append(
                {
                    "bead_index": entry.get("bead_index"),
                    "feature_id": feat_id,
                    "op": entry.get("op"),
                    "strategy": (entry.get("params") or {}).get("strategy"),
                    "strategy_code": (entry.get("params") or {}).get("strategy_code"),
                    "layer_index": int(layer_idx),
                    "z_mm": entry.get("layer_z_mm", 0.0),
                    "length_mm": traj.get("length_mm", 0.0),
                    "points": traj.get("points"),
                }
            )
    return sorted(buckets.values(), key=lambda item: (item["z_mm"], item["index"]))


def plan_ops(features_json, config=None, tools=None, machine=None):
    cfg = normalize_waam_config(_resolve_planner_config(config), features_json)
    dep = (cfg.get("process") or {}).get("deposition") or {}
    path = (cfg.get("process") or {}).get("path") or {}
    heights = cfg.get("heights") or {}
    tools = tools or cfg.get("tools") or []
    tool_index = _build_tool_index(tools)
    primary_tool = _pick_tool(tool_index, "Torch_Primary", "type::deposition", "Torch_Finish", "type::finish")
    machine_meta = machine or {"clearance": float(heights.get("clearance", 30.0)), "rapidZ": float(heights.get("rapidZ", 5.0))}

    layer_height = _as_float(dep.get("layer_height_mm"), 1.0) or 1.0
    min_span_cfg = _as_float(path.get("min_planar_span_mm"))
    if min_span_cfg is None:
        bead_width = _as_float(dep.get("bead_width_mm"), 6.0) or 6.0
        min_span_cfg = bead_width

    filtered_features, dropped_planar = _filter_small_planar_faces(features_json.get("features") or [], float(min_span_cfg))
    filtered_data = dict(features_json)
    filtered_data["features"] = filtered_features

    feature_index = {}
    planner_warnings: List[str] = []
    for feature in filtered_features:
        feat_id = feature.get("id")
        if not feat_id:
            continue
        feat_id = str(feat_id)
        summary = _summarize_feature(feature, layer_height)
        feature_index[feat_id] = summary
        prov = summary.get("planning_provenance") or {}
        if prov.get("bbox_fallback_used"):
            planner_warnings.append(f"Feature {feat_id} uses bbox_fallback geometry.")

    # Build hole keepouts from detected through/cylindrical holes.
    hole_keepouts: List[Dict[str, Any]] = []
    for feat in filtered_features:
        if _as_lower_str(feat.get("type")) not in {"through_hole", "cylindrical_hole"}:
            continue
        center = feat.get("center") or []
        diameter = _as_float(feat.get("diameter"))
        if isinstance(center, (list, tuple)) and len(center) >= 2 and diameter and diameter > 0:
            hole_keepouts.append({"center_xy": (float(center[0]), float(center[1])), "radius": float(diameter) * (0.5 * _HOLE_KEEPOUT_RADIUS_SCALE)})

    sequence: List[Dict[str, Any]] = []
    skipped_features: List[str] = []
    bead_index = 1
    for feature in filtered_features:
        if not _is_deposition_feature(feature):
            feat_id = feature.get("id")
            if feat_id:
                planner_warnings.append(f"Feature {feat_id} skipped for deposition due to feature type '{feature.get('type')}'.")
            continue
        ops, bead_index = _build_feature_ops(
            feature,
            cfg,
            primary_tool,
            bead_start_index=bead_index,
            hole_keepouts=hole_keepouts,
        )
        if not ops:
            feat_id = feature.get("id")
            if feat_id:
                skipped_features.append(str(feat_id))
                planner_warnings.append(f"Feature {feat_id} produced no operations after planning.")
            continue
        sequence.extend(ops)

    sequence = _reorder_sequence_within_layers(sequence, feature_index, strategy=str(path.get("layer_sort", "risk_area")))

    for idx, entry in enumerate(sequence):
        entry["sequence_index"] = idx
        prov = entry.get("planning_provenance") or {}
        feat_id = entry.get("feature_id")
        requested = prov.get("requested_strategy")
        resolved = prov.get("resolved_strategy")
        reason = prov.get("resolution_reason")
        if feat_id and requested and resolved and requested != resolved:
            planner_warnings.append(
                f"Feature {feat_id} strategy resolved from '{requested}' to '{resolved}' ({reason or 'planner_resolution'})."
            )

    layers = _build_layers_from_sequence(sequence)

    interpass = (cfg.get("process") or {}).get("interpass") or {}
    dwell = _as_float(dep.get("dwell_per_layer_s"), 0.0) or 0.0
    max_dwell = _as_float(interpass.get("max_dwell_s"), dwell) or dwell
    dwell = min(dwell, max_dwell) if max_dwell > 0 else dwell
    if dwell > 0 and layers:
        for layer in layers:
            sequence.append(
                {
                    "op": "interpass_cooling",
                    "tool": None,
                    "region": None,
                    "feature_id": None,
                    "feature_type": None,
                    "feature_tags": [],
                    "layer_index": layer["index"],
                    "layer_z_mm": layer["z_mm"],
                    "process_profile": "interpass",
                    "trajectory": None,
                    "params": {
                        "cooling": interpass.get("cooling"),
                        "target_temp_c": interpass.get("target_temp_c"),
                        "max_temp_c": interpass.get("max_temp_c"),
                        "min_temp_c": interpass.get("min_temp_c"),
                        "interpass_dwell_s": dwell,
                        "monitoring": interpass.get("temperature_monitoring"),
                    },
                    "sequence_index": len(sequence),
                }
            )
            layer["operations"].append({"op": "interpass_cooling", "sequence_index": len(sequence) - 1, "feature_id": None})

    metadata = {
        "units": (features_json.get("metadata") or {}).get("units", cfg.get("units", "mm")),
        "wcs": (features_json.get("metadata") or {}).get("wcs") or cfg.get("wcs"),
        "clearance": machine_meta["clearance"],
        "rapidZ": machine_meta["rapidZ"],
        "config_version": cfg.get("version"),
        "machine": cfg.get("machine"),
        "post": cfg.get("post"),
        "deposition_start": (features_json.get("metadata") or {}).get("deposition_start"),
        "mesh_analysis": (features_json.get("metadata") or {}).get("mesh_analysis"),
        "planner_policy": {
            "layer_assignment_mode": _LAYER_ASSIGNMENT_MODE,
            "hole_keepout_radius_scale": _HOLE_KEEPOUT_RADIUS_SCALE,
            "min_planar_span_mm": float(min_span_cfg),
            "risk_weights": dict(_RISK_WEIGHTS),
            "contours_first_default": _as_bool(path.get("contours_first"), True),
            "area_estimate_fallback": _AREA_ESTIMATE_BBOX,
        },
    }
    if dropped_planar:
        metadata["filters"] = {"min_planar_span_mm": float(min_span_cfg), "dropped_planar_faces": dropped_planar}
        planner_warnings.append(
            f"Dropped {len(dropped_planar)} planar feature(s) below min_planar_span_mm={float(min_span_cfg):.3f}."
        )
    if skipped_features:
        metadata.setdefault("filters", {})
        metadata["filters"]["unplannable_features"] = skipped_features

    validation_report = validate_features(filtered_data, cfg)
    validation_warnings = validation_report.get("warnings") or []
    combined_warnings = list(validation_warnings)
    for warning in planner_warnings:
        if warning not in combined_warnings:
            combined_warnings.append(warning)

    return {
        "metadata": metadata,
        "tools": tools,
        "sequence": sequence,
        "feature_index": feature_index,
        "layers": layers,
        "process_parameters": {
            "deposition": cfg.get("process", {}).get("deposition"),
            "finishing": cfg.get("process", {}).get("finishing"),
            "interpass": cfg.get("process", {}).get("interpass"),
            "path": cfg.get("process", {}).get("path"),
            "thermal": cfg.get("process", {}).get("thermal"),
            "auto_scale": cfg.get("process", {}).get("_auto_scale"),
        },
        "material": cfg.get("material"),
        "quality": cfg.get("quality"),
        "safety": cfg.get("safety"),
        "validation": cfg.get("validation"),
        "validation_report": validation_report,
        "warnings": combined_warnings,
        "snippets": cfg.get("snippets"),
    }


# Compatibility alias

def generate_operation_sequence(features_data: Dict[str, Any], tools_available: List[Dict[str, Any]], op_defaults: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    cfg = {"tools": tools_available}
    if op_defaults:
        cfg["defaults"] = op_defaults
    return plan_ops(features_data, config=cfg, tools=tools_available).get("sequence") or []
