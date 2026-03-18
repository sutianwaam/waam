"""Generate a trajectory-aware baseline WAAM NC file from waam_operations.json."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from ..core.paths import _output_root_for, get_nc_output_path
from .contour_clip import clip_paths_to_contours
from .support_trim import build_support_index, support_radius_from_bead, trim_segments_to_support


class NcGenerationError(RuntimeError):
    """Raised when the operations artefact is missing required fields."""


def _load_operations(path: Path) -> Dict:
    if not path.is_file():
        raise FileNotFoundError(f"WAAM operations file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, Mapping):
        raise NcGenerationError("waam_operations.json must contain a JSON object.")
    return dict(data)


def _as_optional_float(value) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _support_constraints_from_process(process: Mapping[str, object]) -> Tuple[Optional[float], Optional[float]]:
    overlap_ratio = None
    overlap_pct = _as_optional_float(process.get("support_min_overlap_pct"))
    if overlap_pct is not None:
        overlap_ratio = max(0.0, min(1.0, overlap_pct / 100.0))
    else:
        overlap_ratio = _as_optional_float(process.get("support_min_overlap_ratio"))
        if overlap_ratio is not None:
            overlap_ratio = max(0.0, min(1.0, overlap_ratio))
    overhang_deg = _as_optional_float(
        process.get("support_max_overhang_angle_deg") or process.get("support_overhang_angle_deg")
    )
    return overlap_ratio, overhang_deg


def _support_trim_settings_from_process(
    process: Mapping[str, object],
) -> Tuple[bool, float, Optional[float], Optional[float]]:
    """
    Extract support trimming controls from process parameters.

    Expected layout (all optional and backward compatible):

        process.deposition.support_trim.enabled: bool
        process.deposition.support_trim.radius_scale: float
        process.deposition.support_trim.sample_step_mm: float
        process.deposition.support_trim.min_segment_length_mm: float

    When this nested mapping is not present, defaults preserve the
    previous behaviour (enabled with radius_scale=1.0 and derived
    sampling parameters).
    """

    trim_cfg = {}
    if isinstance(process.get("support_trim"), Mapping):
        trim_cfg = process.get("support_trim") or {}
    elif isinstance(process.get("deposition"), Mapping):
        dep = process.get("deposition") or {}
        if isinstance(dep.get("support_trim"), Mapping):
            trim_cfg = dep.get("support_trim") or {}

    enabled_raw = trim_cfg.get("enabled")
    enabled = bool(enabled_raw) if enabled_raw is not None else True

    radius_scale = _as_optional_float(trim_cfg.get("radius_scale"))
    if radius_scale is None or not math.isfinite(radius_scale):
        radius_scale = 1.0
    radius_scale = max(0.0, radius_scale)

    sample_step_mm = _as_optional_float(trim_cfg.get("sample_step_mm"))
    if sample_step_mm is not None and sample_step_mm <= 0.0:
        sample_step_mm = None

    min_segment_length_mm = _as_optional_float(trim_cfg.get("min_segment_length_mm"))
    if min_segment_length_mm is not None and min_segment_length_mm <= 0.0:
        min_segment_length_mm = None

    return enabled, radius_scale, sample_step_mm, min_segment_length_mm


def _bbox_from_feature(feature: Mapping) -> Tuple[float, float, float, float]:
    bbox = feature.get("bbox")
    if isinstance(bbox, Mapping):
        try:
            return (
                float(bbox["xmin"]),
                float(bbox["xmax"]),
                float(bbox["ymin"]),
                float(bbox["ymax"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise NcGenerationError(f"Invalid bbox structure for feature {feature.get('id')}") from exc
    if isinstance(bbox, Sequence) and len(bbox) >= 4:
        try:
            return float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        except (TypeError, ValueError) as exc:
            raise NcGenerationError(f"Invalid bbox values for feature {feature.get('id')}") from exc
    raise NcGenerationError(f"Feature {feature.get('id')} is missing bbox data required for NC generation.")


def _collect_features(feature_index: Mapping[str, Mapping]) -> List[Tuple[str, Tuple[float, float, float, float]]]:
    priority: List[Tuple[str, Tuple[float, float, float, float]]] = []
    fallback: List[Tuple[str, Tuple[float, float, float, float]]] = []
    for feature_id, feature in feature_index.items():
        try:
            bbox = _bbox_from_feature(feature)
        except NcGenerationError:
            continue
        ftype = str(feature.get("type") or "").lower()
        if any(keyword in ftype for keyword in ("pocket", "planar", "freeform")):
            priority.append((feature_id, bbox))
        else:
            fallback.append((feature_id, bbox))
    if priority:
        return sorted(priority, key=lambda item: (item[1][2], item[1][0]))
    if fallback:
        return sorted(fallback, key=lambda item: (item[1][2], item[1][0]))
    raise NcGenerationError("No usable features with bounding boxes found for NC generation.")


def _serpentine_lines(
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    spacing: float,
    reverse: bool,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Generate serpentine hatch lines that cover the rectangular region."""
    width = max(0.1, ymax - ymin)
    passes = max(1, int(math.ceil(width / max(spacing, 0.1))))
    if passes == 1:
        y_values = [ymin + width / 2.0]
    else:
        step = width / (passes - 1)
        y_values = [ymin + i * step for i in range(passes)]
    if reverse:
        y_values = list(reversed(y_values))

    lines: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    direction_positive = True
    for y in y_values:
        if direction_positive:
            start, end = (xmin, y), (xmax, y)
        else:
            start, end = (xmax, y), (xmin, y)
        lines.append((start, end))
        direction_positive = not direction_positive
    return lines


def _perimeter_lines(
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    points = [
        (xmin, ymin),
        (xmax, ymin),
        (xmax, ymax),
        (xmin, ymax),
        (xmin, ymin),
    ]
    return [((points[i][0], points[i][1]), (points[i + 1][0], points[i + 1][1])) for i in range(len(points) - 1)]


def _format_xy(x: float, y: float) -> str:
    return f"G1 X{x:.3f} Y{y:.3f}"


def _effective_spacing(bead_width: float, overlap: float) -> float:
    return max(0.1, bead_width * max(0.1, 1.0 - overlap))


def _offset_bbox(
    bbox: Tuple[float, float, float, float],
    offset: float,
) -> Optional[Tuple[float, float, float, float]]:
    xmin, xmax, ymin, ymax = bbox
    inset = max(0.0, offset)
    nxmin, nxmax = xmin + inset, xmax - inset
    nymin, nymax = ymin + inset, ymax - inset
    if nxmax <= nxmin or nymax <= nymin:
        return None
    return (nxmin, nxmax, nymin, nymax)


def _rotate_point(
    x: float,
    y: float,
    *,
    angle_rad: float,
    origin: Tuple[float, float],
    inverse: bool = False,
) -> Tuple[float, float]:
    ox, oy = origin
    dx, dy = x - ox, y - oy
    if inverse:
        angle_rad = -angle_rad
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    rx = dx * cos_a - dy * sin_a
    ry = dx * sin_a + dy * cos_a
    return ox + rx, oy + ry


def _rotated_serpentine(
    bbox: Tuple[float, float, float, float],
    angle_deg: float,
    spacing: float,
    reverse: bool,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Serpentine hatch rotated around the bbox center."""
    angle_rad = math.radians(angle_deg % 180.0)
    cx = (bbox[0] + bbox[1]) / 2.0
    cy = (bbox[2] + bbox[3]) / 2.0

    corners = [
        _rotate_point(x, y, angle_rad=angle_rad, origin=(cx, cy))
        for x, y in ((bbox[0], bbox[2]), (bbox[0], bbox[3]), (bbox[1], bbox[2]), (bbox[1], bbox[3]))
    ]
    rx_min = min(p[0] for p in corners)
    rx_max = max(p[0] for p in corners)
    ry_min = min(p[1] for p in corners)
    ry_max = max(p[1] for p in corners)

    hatch = _serpentine_lines(rx_min, rx_max, ry_min, ry_max, spacing, reverse)
    return [
        (
            _rotate_point(sx, sy, angle_rad=angle_rad, origin=(cx, cy), inverse=True),
            _rotate_point(ex, ey, angle_rad=angle_rad, origin=(cx, cy), inverse=True),
        )
        for (sx, sy), (ex, ey) in hatch
    ]


def _build_paths_for_bbox(
    bbox: Tuple[float, float, float, float],
    *,
    bead_width: float,
    overlap: float,
    angle_deg: float,
    contours: int,
    reverse: bool,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Create perimeter + infill paths for a rectangular bbox."""
    spacing = _effective_spacing(bead_width, overlap)
    paths: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []

    last_offset = 0.0
    for idx in range(max(0, contours)):
        inset = bead_width * 0.5 + idx * spacing
        obox = _offset_bbox(bbox, inset)
        if obox is None:
            break
        last_offset = inset
        paths.extend(_perimeter_lines(*obox))

    inner_bbox = _offset_bbox(bbox, last_offset + bead_width * 0.5)
    if inner_bbox:
        paths.extend(
            _rotated_serpentine(
                inner_bbox,
                angle_deg=angle_deg,
                spacing=spacing,
                reverse=reverse,
            )
        )
    return paths


def _with_leads(
    paths: List[Tuple[Tuple[float, float], Tuple[float, float]]],
    *,
    lead_in: float,
    lead_out: float,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Add lead-in/out segments to the path list."""
    if not paths:
        return paths

    extended: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    first = paths[0]
    fx, fy = first[0]
    sx, sy = first[1]
    dx, dy = sx - fx, sy - fy
    seg_len = math.hypot(dx, dy)

    if lead_in > 0 and seg_len > 1e-6:
        ratio = min(lead_in, seg_len * 0.4) / seg_len
        start_pre = (fx - dx * ratio, fy - dy * ratio)
        extended.append((start_pre, (fx, fy)))

    extended.extend(paths)

    if lead_out > 0:
        lx, ly = paths[-1][0]
        ex, ey = paths[-1][1]
        dx2, dy2 = ex - lx, ey - ly
        seg_len2 = math.hypot(dx2, dy2)
        if seg_len2 > 1e-6:
            ratio2 = lead_out / seg_len2
            end_post = (ex + dx2 * ratio2, ey + dy2 * ratio2)
            extended.append(((ex, ey), end_post))

    return extended


def _rotate_paths_to_start(
    paths: List[Tuple[Tuple[float, float], Tuple[float, float]]],
    start_xy: Tuple[float, float],
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    if not paths:
        return paths
    sx, sy = start_xy
    best_idx = 0
    best_dist = None
    for idx, (start, _end) in enumerate(paths):
        dx = start[0] - sx
        dy = start[1] - sy
        dist = dx * dx + dy * dy
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = idx
    if best_idx == 0:
        return paths
    return paths[best_idx:] + paths[:best_idx]


def _sanitize_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "feature"


def _start_point_from_metadata(metadata: Mapping[str, object]) -> Optional[Tuple[float, float, float]]:
    dep = metadata.get("deposition_start")
    point = None
    if isinstance(dep, Mapping):
        point = dep.get("point_wcs") or dep.get("point") or dep.get("start_point")
    else:
        point = dep
    if isinstance(point, Sequence) and len(point) >= 2:
        try:
            x = float(point[0])
            y = float(point[1])
            z = float(point[2]) if len(point) > 2 else 0.0
            return (x, y, z)
        except Exception:
            return None
    return None


def _load_baseline_config(data: Dict, ops_path: Path) -> Dict:
    """Extract and normalize configuration parameters for NC generation."""
    metadata = data.get("metadata") or {}
    process_params = data.get("process_parameters") or {}
    process = process_params.get("deposition") or {}
    interpass = process_params.get("interpass") or {}

    layer_height = float(
        process.get("layer_height_mm")
        or process.get("layer_height")
        or 1.0
    )
    bead_width = float(process.get("bead_width_mm") or process.get("bead_width") or 6.0)
    wire_feed = float(process.get("wire_feed_mm_min") or 3200.0)
    travel_speed = float(process.get("travel_speed_mm_min") or 300.0)
    z_feed = float(
        (data.get("defaults") or {}).get("roughing", {}).get("feed_z")
        or (data.get("defaults") or {}).get("finishing", {}).get("feed_z")
        or 200.0
    )
    dwell_per_layer = float(process.get("dwell_per_layer_s") or 1.5)
    cooling_time = float(interpass.get("max_dwell_s") or 45.0)

    mesh_global = (metadata.get("mesh_analysis") or {}).get("global") or {}
    bbox_dims = mesh_global.get("bbox_dimensions", [0, 0, layer_height])
    span_z = float(bbox_dims[2] or layer_height)
    total_layers = max(1, int(math.ceil(span_z / layer_height)))

    post = metadata.get("post") or data.get("post") or {}
    post_cmds = {
        "torch_on": post.get("torch_on", "M62 P0"),
        "torch_off": post.get("torch_off", "M63 P0"),
        "gas_on": post.get("gas_on", "M64 P1"),
        "gas_off": post.get("gas_off", "M65 P1"),
        "wire_feed": post.get("wire_feed") or "M68 E0 Q{wire_feed:.0f}",
        "travel_speed": post.get("travel_speed") or "F{travel_speed:.0f}",
        "interpass_pause": post.get("interpass_pause") or "G4 P{interpass_dwell:.1f}",
        "gas_preflow": float(post.get("gas_preflow_s", 1.0)),
        "gas_postflow": float(post.get("gas_postflow_s", 2.0)),
        "arc_start_delay": float(post.get("arc_start_delay_s", 0.4)),
        "arc_stop_delay": float(post.get("arc_stop_delay_s", 0.2)),
    }

    clearance = float(metadata.get("clearance", 15.0))
    rapid_z = float(metadata.get("rapidZ", clearance))
    start_point = _start_point_from_metadata(metadata)
    start_xy = (start_point[0], start_point[1]) if start_point else None

    return {
        "metadata": metadata,
        "process_params": process_params,
        "layer_height": layer_height,
        "bead_width": bead_width,
        "wire_feed": wire_feed,
        "travel_speed": travel_speed,
        "z_feed": z_feed,
        "dwell_per_layer": dwell_per_layer,
        "cooling_time": cooling_time,
        "total_layers": total_layers,
        "post": post_cmds,
        "clearance": clearance,
        "rapid_z": rapid_z,
        "start_xy": start_xy,
    }


def _prepare_layer_ops(data: Dict, total_layers: int) -> Tuple[Dict[int, List[Dict]], List[int], int]:
    """Organize full operation sequence into layers, preserving order."""
    layer_ops: Dict[int, List[Dict]] = {}
    max_layer_idx = 0
    sequence = data.get("sequence") or []
    if isinstance(sequence, Sequence):
        for entry in sequence:
            if not isinstance(entry, Mapping):
                continue
            layer_idx = int(entry.get("layer_index") or 0)
            item = dict(entry)
            item.setdefault("sequence_index", len(layer_ops.get(layer_idx, [])))
            layer_ops.setdefault(layer_idx, []).append(item)
            max_layer_idx = max(max_layer_idx, layer_idx)

    for ops in layer_ops.values():
        ops.sort(key=lambda e: int(e.get("sequence_index", 10**9)))

    layer_order = sorted(layer_ops.keys())
    final_total_layers = total_layers
    if layer_order:
        final_total_layers = max(total_layers, max_layer_idx + 1)

    return layer_ops, layer_order, final_total_layers


def _write_program(path: Path, preamble: List[str], body: List[str], footer: List[str]) -> None:
    lines = list(preamble)
    lines.extend(body)
    lines.extend(footer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _trajectory_segments_from_entry(entry: Mapping) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    traj = entry.get("trajectory") or {}
    pts = traj.get("points") or []
    if len(pts) < 2:
        return []

    out: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for i in range(len(pts) - 1):
        p0 = pts[i]
        p1 = pts[i + 1]
        try:
            out.append(((float(p0[0]), float(p0[1])), (float(p1[0]), float(p1[1]))))
        except Exception:
            continue
    return out


def _entry_layer_z(entry: Mapping, default_z: Optional[float] = None) -> Optional[float]:
    z = _as_optional_float(entry.get("layer_z_mm"))
    if z is not None:
        return z
    traj = entry.get("trajectory") or {}
    pts = traj.get("points") or []
    if pts:
        try:
            return float(pts[0][2])
        except Exception:
            pass
    return default_z


def _format_post_value(template_or_cmd: str, **kwargs) -> str:
    try:
        return str(template_or_cmd).format(**kwargs)
    except Exception:
        return str(template_or_cmd)


def _operation_post_cfg(entry: Mapping, cfg: Dict) -> Dict:
    base = dict(cfg["post"])
    op_post = ((entry.get("params") or {}).get("post") or {})
    if isinstance(op_post, Mapping):
        base.update(op_post)
    base["wire_feed"] = _format_post_value(
        base["wire_feed"],
        wire_feed=((entry.get("params") or {}).get("wire_feed_mm_min") or cfg["wire_feed"]),
    )
    base["travel_speed"] = _format_post_value(
        base["travel_speed"],
        travel_speed=((entry.get("params") or {}).get("travel_speed_mm_min") or cfg["travel_speed"]),
    )
    return base


def _postprocess_paths(
    paths: List[Tuple[Tuple[float, float], Tuple[float, float]]],
    *,
    start_xy: Optional[Tuple[float, float]],
    used_start_point: bool,
    lead_in_mm: float,
    lead_out_mm: float,
    support_ctx: Optional[Tuple[Dict, float, float, float]],
) -> Tuple[List[Tuple[Tuple[float, float], Tuple[float, float]]], bool]:
    start_point_consumed = False

    if start_xy and not used_start_point and paths:
        paths = _rotate_paths_to_start(paths, start_xy)
        start_point_consumed = True

    paths = _with_leads(paths, lead_in=lead_in_mm, lead_out=lead_out_mm)

    if support_ctx:
        support_index, support_radius, support_sample_step, support_min_segment = support_ctx
        if support_index is not None and support_radius > 0.0:
            paths = trim_segments_to_support(
                paths,
                support_index,
                support_radius,
                sample_step=support_sample_step,
                min_segment_length=support_min_segment,
            )

    return paths, start_point_consumed


def _generate_feature_path_from_bbox(
    bbox: Tuple[float, float, float, float],
    cfg: Dict,
    angle: float,
    contours: int,
    infill_overlap: float,
    reverse: bool,
    contour_clip_data: Optional[Tuple[List[Tuple[float, float]], List[List[Tuple[float, float]]]]] = None,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    paths = _build_paths_for_bbox(
        bbox,
        bead_width=cfg["bead_width"],
        overlap=infill_overlap,
        angle_deg=angle,
        contours=contours,
        reverse=reverse,
    )

    if contour_clip_data is not None:
        outer_poly, hole_polys = contour_clip_data
        paths = clip_paths_to_contours(paths, outer_poly, hole_polys)

    return paths


def _is_hole_feature(feature: Mapping[str, object]) -> bool:
    ftype = str(feature.get("type") or "").strip().lower()
    return ftype in {"through_hole", "cylindrical_hole"}


def _hole_z_span(feature: Mapping[str, object]) -> Tuple[Optional[float], Optional[float]]:
    z_top = _as_optional_float(feature.get("z_top"))
    depth = _as_optional_float(feature.get("depth"))
    if z_top is None:
        center = feature.get("center")
        if isinstance(center, Sequence) and len(center) >= 3:
            z_top = _as_optional_float(center[2])
    if z_top is not None and depth is not None and depth > 0.0:
        z_other = z_top - depth
        return (min(z_top, z_other), max(z_top, z_other))

    z = _as_optional_float(feature.get("z"))
    if z is not None:
        return (z, z)
    if z_top is not None:
        return (z_top, z_top)
    return (None, None)


def _hole_polygon(feature: Mapping[str, object], *, segments: int = 48) -> Optional[List[Tuple[float, float]]]:
    center = feature.get("center")
    if not isinstance(center, Sequence) or len(center) < 2:
        return None
    cx = _as_optional_float(center[0])
    cy = _as_optional_float(center[1])
    diameter = _as_optional_float(feature.get("diameter"))
    if cx is None or cy is None or diameter is None:
        return None
    radius = max(0.0, float(diameter) * 0.5)
    if radius <= 1e-6:
        return None

    n = max(12, int(segments))
    pts = []
    for idx in range(n):
        theta = 2.0 * math.pi * (idx / n)
        pts.append((cx + radius * math.cos(theta), cy + radius * math.sin(theta)))
    pts.append(pts[0])
    return pts


def _hole_polygons_for_layer(
    feature_index: Mapping[str, Mapping[str, object]],
    z_height: float,
    *,
    layer_height: float,
) -> List[List[Tuple[float, float]]]:
    out: List[List[Tuple[float, float]]] = []
    z_tol = max(0.1, abs(layer_height) * 0.5)

    for feature in feature_index.values():
        if not isinstance(feature, Mapping) or not _is_hole_feature(feature):
            continue
        z_min, z_max = _hole_z_span(feature)
        if z_min is not None and z_max is not None:
            if z_height < (z_min - z_tol) or z_height > (z_max + z_tol):
                continue
        poly = _hole_polygon(feature)
        if poly:
            out.append(poly)

    return out


def _clip_paths_for_layer(
    paths: List[Tuple[Tuple[float, float], Tuple[float, float]]],
    contour_clip_data: Optional[Tuple[Optional[List[Tuple[float, float]]], List[List[Tuple[float, float]]]]],
    hole_polys: List[List[Tuple[float, float]]],
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    if not paths:
        return paths

    outer_poly: Optional[List[Tuple[float, float]]] = None
    inner_polys: List[List[Tuple[float, float]]] = []
    if contour_clip_data is not None:
        outer_poly, contour_holes = contour_clip_data
        if contour_holes:
            inner_polys.extend(contour_holes)
    if hole_polys:
        inner_polys.extend(hole_polys)

    if outer_poly is None and not inner_polys:
        return paths
    return clip_paths_to_contours(paths, outer_poly, inner_polys)


def _generate_path_gcode(
    paths: List[Tuple[Tuple[float, float], Tuple[float, float]]],
    z_height: float,
    cfg: Dict,
    ramp_feed_fraction: float,
    entry: Optional[Mapping] = None,
    split_on_discontinuity: bool = False,
) -> List[str]:
    if not paths:
        return []

    post = _operation_post_cfg(entry or {}, cfg)
    travel_speed = float((entry.get("params") or {}).get("travel_speed_mm_min") or cfg["travel_speed"]) if entry else cfg["travel_speed"]
    clearance = cfg["clearance"]
    rapid_z = cfg["rapid_z"]
    z_feed = cfg["z_feed"]

    ramp_feed = max(10.0, travel_speed * max(0.05, ramp_feed_fraction))
    ramp_feed_cmd = f"F{ramp_feed:.0f}"

    lines = []
    start_point = paths[0][0]

    lines.append(f"G0 X{start_point[0]:.3f} Y{start_point[1]:.3f} Z{clearance:.3f}")
    lines.append(post["gas_on"])
    lines.append(f"G4 P{post['gas_preflow']:.1f}")
    lines.append(post["wire_feed"])
    lines.append(post["torch_on"])
    lines.append(f"G4 P{post['arc_start_delay']:.1f}")
    lines.append(f"G1 Z{z_height:.3f} F{z_feed:.1f}")

    def _reposition_with_torch_off(target_x: float, target_y: float) -> None:
        # Disjoint segments (e.g. split by holes) must be traversed with arc off.
        lines.append(post["torch_off"])
        lines.append(f"G4 P{post['arc_stop_delay']:.1f}")
        lines.append(f"G0 Z{clearance:.3f}")
        lines.append(f"G0 X{target_x:.3f} Y{target_y:.3f} Z{rapid_z:.3f}")
        lines.append(post["torch_on"])
        lines.append(f"G4 P{post['arc_start_delay']:.1f}")
        lines.append(f"G1 Z{z_height:.3f} F{z_feed:.1f}")

    current_point = start_point
    first_move = True
    for (sx, sy), (ex, ey) in paths:
        if abs(sx - current_point[0]) > 1e-6 or abs(sy - current_point[1]) > 1e-6:
            if split_on_discontinuity:
                _reposition_with_torch_off(sx, sy)
                current_point = (sx, sy)
                first_move = True
            else:
                if first_move:
                    lines.append(ramp_feed_cmd)
                lines.append(_format_xy(sx, sy))
                current_point = (sx, sy)
        if first_move:
            lines.append(ramp_feed_cmd)
        else:
            lines.append(post["travel_speed"])
        lines.append(_format_xy(ex, ey))
        current_point = (ex, ey)
        first_move = False

    lines.append(f"G4 P{cfg['dwell_per_layer']:.1f}")
    lines.append(post["torch_off"])
    lines.append(f"G4 P{post['arc_stop_delay']:.1f}")
    lines.append(post["gas_off"])
    lines.append(f"G4 P{post['gas_postflow']:.1f}")
    lines.append(f"G0 Z{clearance:.3f}")
    lines.append(f"G0 X{current_point[0]:.3f} Y{current_point[1]:.3f} Z{rapid_z:.3f}")
    lines.append("")

    return lines


def _generate_interpass_gcode(entry: Mapping, cfg: Dict) -> List[str]:
    params = entry.get("params") or {}
    dwell = float(params.get("interpass_dwell_s") or cfg["cooling_time"] or 0.0)
    if dwell <= 0.0:
        return []

    post = _operation_post_cfg(entry, cfg)
    pause_cmd = _format_post_value(post.get("interpass_pause", "G4 P{interpass_dwell:.1f}"), interpass_dwell=dwell)

    layer_idx = entry.get("layer_index")
    layer_z = entry.get("layer_z_mm")
    header = f"(Interpass cooling layer={layer_idx} z={layer_z})"

    return [
        header,
        pause_cmd,
        "",
    ]


def _load_dsl_contours(
    dsl_path: Optional[Path],
    ops_path: Optional[Path] = None,
    wcs_placement: Optional[Mapping[str, object]] = None,
) -> Dict[float, Tuple[Optional[List[Tuple[float, float]]], List[List[Tuple[float, float]]]]]:
    """Load WAAM DSL and build a { z_height → (outer_polygon, [hole_polygons]) } lookup."""
    if dsl_path is None and ops_path is not None:
        output_root = _output_root_for(ops_path)
        dsl_dir = output_root / "dsl_waam"
        if dsl_dir.is_dir():
            for pattern in ("*.waam", "*.llm_waam"):
                candidates = sorted(dsl_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
                if candidates:
                    dsl_path = candidates[0]
                    break
    if dsl_path is None or not Path(dsl_path).is_file():
        return {}

    try:
        from .waam_dsl_nc import parse_waam_dsl
        doc = parse_waam_dsl(dsl_path)
    except Exception:
        return {}

    def _transform_point_to_wcs(pt_world: Tuple[float, float, float], wcs: Mapping[str, object]) -> Tuple[float, float, float]:
        orig = wcs.get("origin_world", [0.0, 0.0, 0.0])
        axes = wcs.get("axes_world", {})
        x_axis = axes.get("x", [1.0, 0.0, 0.0])
        y_axis = axes.get("y", [0.0, 1.0, 0.0])
        z_axis = axes.get("z", [0.0, 0.0, 1.0])

        dx = pt_world[0] - orig[0]
        dy = pt_world[1] - orig[1]
        dz = pt_world[2] - orig[2]

        wx = dx * x_axis[0] + dy * x_axis[1] + dz * x_axis[2]
        wy = dx * y_axis[0] + dy * y_axis[1] + dz * y_axis[2]
        wz = dx * z_axis[0] + dy * z_axis[1] + dz * z_axis[2]
        return (wx, wy, wz)

    lookup: Dict[float, Tuple[Optional[List[Tuple[float, float]]], List[List[Tuple[float, float]]]]] = {}
    for layer in doc.layers:
        if layer.z is None:
            continue

        outer = None
        holes: List[List[Tuple[float, float]]] = []
        wcs_z_layer = None

        for contour in layer.contours:
            if len(contour.points) < 3:
                continue

            transformed_pts = []
            for (x, y) in contour.points:
                if wcs_placement:
                    wx, wy, wz = _transform_point_to_wcs((x, y, layer.z), wcs_placement)
                    transformed_pts.append((wx, wy))
                    if wcs_z_layer is None:
                        wcs_z_layer = wz
                else:
                    transformed_pts.append((x, y))
                    if wcs_z_layer is None:
                        wcs_z_layer = layer.z

            if contour.is_outer:
                outer = transformed_pts
            else:
                holes.append(transformed_pts)

        if outer is not None or holes:
            if wcs_z_layer is None:
                wcs_z_layer = layer.z
            lookup[round(wcs_z_layer, 4)] = (outer, holes)
    return lookup


def _find_contour_for_z(
    z: float,
    contour_lookup: Dict[float, Tuple[Optional[List[Tuple[float, float]]], List[List[Tuple[float, float]]]]],
    tolerance: float = 0.1,
) -> Optional[Tuple[List[Tuple[float, float]], List[List[Tuple[float, float]]]]]:
    """Find the closest matching contour data for a given Z height."""
    if not contour_lookup:
        return None
    z_r = round(z, 4)
    if z_r in contour_lookup:
        return contour_lookup[z_r]
    best_z = None
    best_dist = tolerance + 1.0
    for cz in contour_lookup:
        dist = abs(cz - z)
        if dist < best_dist:
            best_dist = dist
            best_z = cz
    if best_z is not None and best_dist <= tolerance:
        return contour_lookup[best_z]
    return None


def generate_baseline_nc(
    operations_path: Path | str,
    *,
    output_path: Path | str | None = None,
    infill_overlap: float = 0.15,
    infill_angle_start: float = 0.0,
    infill_angle_step: float = 90.0,
    contours: int = 1,
    lead_in_mm: float = 5.0,
    lead_out_mm: float = 5.0,
    ramp_feed_fraction: float = 0.4,
    split_layers: bool = False,
    split_features: bool = False,
    chunk_dir: Path | str | None = None,
    write_manifest: bool = False,
    support_trim_enabled: Optional[bool] = None,
    support_radius_scale: Optional[float] = None,
    support_sample_step_mm: Optional[float] = None,
    support_min_segment_length_mm: Optional[float] = None,
    waam_dsl_path: Path | str | None = None,
    discontinuity_split_mode: str = "auto",
) -> Path:
    """
    Create a multi-layer NC program from waam_operations.json.

    Priority:
    1. Use explicit planner trajectories from sequence[*].trajectory.points
    2. Fallback to bbox-based synthesis
    3. Optionally clip bbox paths against WAAM DSL contours
    """
    ops_path = Path(operations_path).expanduser().resolve()
    data = _load_operations(ops_path)
    feature_index: MutableMapping[str, Mapping] = data.get("feature_index") or {}

    cfg = _load_baseline_config(data, ops_path)
    split_mode = str(discontinuity_split_mode or "auto").strip().lower()
    if split_mode not in {"auto", "always", "never"}:
        raise NcGenerationError(
            f"Invalid discontinuity_split_mode '{discontinuity_split_mode}'. "
            "Expected one of: auto, always, never."
        )

    process_params = cfg["process_params"]
    support_overlap_ratio, support_overhang_deg = _support_constraints_from_process(process_params)
    cfg_enabled, cfg_radius_scale, cfg_sample_step_mm, cfg_min_seg_mm = _support_trim_settings_from_process(
        process_params
    )
    if support_trim_enabled is None:
        support_trim_enabled = cfg_enabled
    if support_radius_scale is None:
        support_radius_scale = cfg_radius_scale
    if support_sample_step_mm is None:
        support_sample_step_mm = cfg_sample_step_mm
    if support_min_segment_length_mm is None:
        support_min_segment_length_mm = cfg_min_seg_mm

    support_radius = support_radius_from_bead(
        cfg["bead_width"],
        min_overlap_ratio=support_overlap_ratio,
        max_overhang_angle_deg=support_overhang_deg,
        layer_height=cfg["layer_height"],
    )
    if not support_trim_enabled:
        support_radius = 0.0
    elif support_radius_scale is not None:
        support_radius = max(0.0, support_radius * float(support_radius_scale))

    support_sample_step = None
    support_min_segment = None
    if support_radius > 0.0:
        support_sample_step = (
            float(support_sample_step_mm)
            if support_sample_step_mm is not None
            else max(0.1, support_radius * 0.5)
        )
        support_min_segment = (
            float(support_min_segment_length_mm)
            if support_min_segment_length_mm is not None
            else max(0.05, support_radius * 0.05)
        )

    wcs_placement = (data.get("metadata") or {}).get("wcs", {}).get("placement")
    _dsl_p = Path(waam_dsl_path) if waam_dsl_path else None
    contour_lookup = _load_dsl_contours(_dsl_p, ops_path, wcs_placement)

    layer_ops, layer_order, total_layers = _prepare_layer_ops(data, cfg["total_layers"])

    def _bbox_for_feature(feature_id: str) -> Tuple[float, float, float, float]:
        feature = feature_index.get(feature_id, {})
        return _bbox_from_feature(feature)

    preamble = [
        "%",
        "(WAAM baseline NC - generated locally)",
        f"(Source: {ops_path.name})",
        "G21",
        "G90",
        "G17",
        "G54",
        "G94",
        "G40",
        "G49",
        "T1 M6 (Select Tool 1 for simulation)",
        "",
    ]
    program_lines = list(preamble)
    footer = [
        cfg["post"]["torch_off"],
        cfg["post"]["gas_off"],
        f"G0 Z{cfg['clearance']:.3f}",
        "G0 X0.0 Y0.0",
        "M30",
        "%",
    ]

    layer_chunks: List[Tuple[int, List[str]]] = []
    feature_chunks: List[Tuple[int, str, List[str]]] = []

    support_index = None
    used_start_point = False

    def _iter_layers() -> Iterable[Tuple[int, float, List[Dict]]]:
        if layer_order:
            ordered_layers = list(layer_order)
            layer_z_map: Dict[int, float] = {}
            for layer_idx in ordered_layers:
                z_height = None
                for entry in layer_ops.get(layer_idx, []):
                    z_height = _entry_layer_z(entry)
                    if z_height is not None:
                        break
                if z_height is None:
                    z_height = (layer_idx + 1) * cfg["layer_height"]
                layer_z_map[layer_idx] = z_height

            ordered_layers.sort(key=lambda idx: layer_z_map.get(idx, (idx + 1) * cfg["layer_height"]))
            for layer_idx in ordered_layers:
                yield layer_idx, layer_z_map[layer_idx], list(layer_ops.get(layer_idx, []))
        else:
            # Fallback only when there is no planner sequence.
            for layer_idx in range(total_layers):
                z_height = (layer_idx + 1) * cfg["layer_height"]
                collected = []
                for feat_id, _bbox in _collect_features(feature_index):
                    collected.append(
                        {
                            "feature_id": feat_id,
                            "region": feat_id,
                            "layer_index": layer_idx,
                            "layer_z_mm": z_height,
                            "op": "deposit_infill",
                            "sequence_index": len(collected),
                        }
                    )
                yield layer_idx, z_height, collected

    for layer_idx, z_height, layer_entries in _iter_layers():
        if not layer_entries:
            continue

        layer_support_segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        angle = (infill_angle_start + layer_idx * infill_angle_step) % 180.0

        layer_block: List[str] = [f"(--- LAYER {layer_idx + 1}/{total_layers} Z={z_height:.2f}mm angle={angle:.1f} ---)"]
        program_lines.append(layer_block[0])

        clip_data = _find_contour_for_z(z_height, contour_lookup)
        hole_clip_polys = _hole_polygons_for_layer(feature_index, z_height, layer_height=cfg["layer_height"])
        layer_feature_ids = {
            str(entry.get("feature_id") or entry.get("region") or "")
            for entry in layer_entries
            if isinstance(entry, Mapping) and str(entry.get("op") or "") != "interpass_cooling"
        }
        layer_feature_ids.discard("")
        layer_has_voids = bool((clip_data and clip_data[1]) or hole_clip_polys)
        layer_has_multiple_regions = len(layer_feature_ids) > 1
        split_on_discontinuity = layer_has_voids or layer_has_multiple_regions
        if split_mode == "always":
            split_on_discontinuity = True
        elif split_mode == "never":
            split_on_discontinuity = False

        for feature_order, entry in enumerate(layer_entries):
            if not isinstance(entry, Mapping):
                continue

            op_name = str(entry.get("op") or "")
            feature_id = str(entry.get("feature_id") or entry.get("region") or f"layer_{layer_idx}_entry_{feature_order}")

            if op_name == "interpass_cooling":
                seq = _generate_interpass_gcode(entry, cfg)
                if seq:
                    program_lines.extend(seq)
                    layer_block.extend(seq)
                continue

            paths = _trajectory_segments_from_entry(entry)
            path_source = "trajectory"

            if not paths:
                try:
                    bbox = _bbox_for_feature(feature_id)
                except NcGenerationError:
                    continue

                paths = _generate_feature_path_from_bbox(
                    bbox,
                    cfg,
                    angle,
                    contours,
                    infill_overlap,
                    reverse=((layer_idx + feature_order) % 2 == 1),
                    contour_clip_data=None,
                )
                path_source = "bbox"

            paths = _clip_paths_for_layer(paths, clip_data, hole_clip_polys)

            support_ctx = (support_index, support_radius, support_sample_step, support_min_segment) if support_index else None
            paths, start_consumed = _postprocess_paths(
                paths,
                start_xy=cfg["start_xy"],
                used_start_point=used_start_point,
                lead_in_mm=lead_in_mm,
                lead_out_mm=lead_out_mm,
                support_ctx=support_ctx,
            )
            paths = _clip_paths_for_layer(paths, clip_data, hole_clip_polys)

            if start_consumed:
                used_start_point = True

            if not paths:
                continue

            layer_support_segments.extend(paths)

            z_entry = _entry_layer_z(entry, default_z=z_height)
            if z_entry is None:
                z_entry = z_height

            feature_header = f"(Feature {feature_id} op={op_name or 'deposit'} source={path_source})"
            seq = [feature_header] + _generate_path_gcode(
                paths,
                z_entry,
                cfg,
                ramp_feed_fraction,
                entry=entry,
                split_on_discontinuity=split_on_discontinuity,
            )

            program_lines.extend(seq)
            layer_block.extend(seq)
            feature_chunks.append((layer_idx, feature_id, list(seq)))

        if support_radius > 0.0:
            support_index = (
                build_support_index(layer_support_segments, support_radius)
                if layer_support_segments
                else None
            )
        layer_chunks.append((layer_idx, list(layer_block)))

    program_lines.extend(footer)

    if output_path:
        target_path = Path(output_path).expanduser().resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        root = _output_root_for(ops_path)
        target_path = get_nc_output_path(root, prefer_existing=False, create=True)

    target_path.write_text("\n".join(program_lines), encoding="utf-8")

    if split_layers or split_features:
        chunks_root = Path(chunk_dir).expanduser().resolve() if chunk_dir else target_path.parent / "chunks"
        manifest: Dict[str, object] = {
            "source_operations": str(ops_path),
            "baseline_nc": str(target_path),
            "chunks_root": str(chunks_root),
            "layers": [],
            "features": [],
        }
        if split_layers:
            layer_dir = chunks_root / "layers"
            for layer_idx, body in layer_chunks:
                fname = f"layer_{layer_idx + 1:03d}.nc"
                path = layer_dir / fname
                _write_program(path, preamble, body, footer)
                if write_manifest:
                    manifest["layers"].append(
                        {
                            "layer_index": layer_idx,
                            "path": str(path),
                        }
                    )
        if split_features:
            feat_dir = chunks_root / "features"
            for layer_idx, feature_id, body in feature_chunks:
                fname = f"layer_{layer_idx + 1:03d}_{_sanitize_name(feature_id)}.nc"
                path = feat_dir / fname
                _write_program(path, preamble, body, footer)
                if write_manifest:
                    manifest["features"].append(
                        {
                            "layer_index": layer_idx,
                            "feature_id": feature_id,
                            "path": str(path),
                        }
                    )
        if write_manifest and (manifest["layers"] or manifest["features"]):
            manifest["created_at"] = datetime.now(timezone.utc).isoformat()
            manifest_path = chunks_root / "manifest.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return target_path


__all__ = ["NcGenerationError", "generate_baseline_nc"]
