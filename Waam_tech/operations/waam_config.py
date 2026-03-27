from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Mapping, Optional, Sequence


def deep_merge(*mappings: Mapping[str, Any]) -> Dict[str, Any]:
    def _merge(a: Mapping[str, Any], b: Mapping[str, Any]) -> Dict[str, Any]:
        out = dict(a)
        for key, value in (b or {}).items():
            if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
                out[key] = _merge(out[key], value)
            else:
                out[key] = deepcopy(value)
        return out

    result: Dict[str, Any] = {}
    for mapping in mappings:
        if isinstance(mapping, Mapping):
            result = _merge(result, mapping)
    return result


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _lerp(a: float, b: float, t: float) -> float:
    return float(a) + (float(b) - float(a)) * float(t)


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _max_span_from_features_data(features_data: Mapping[str, Any]) -> Optional[float]:
    metadata = features_data.get("metadata") or {}
    mesh_data = metadata.get("mesh_analysis") or metadata.get("mesh") or {}
    global_stats = mesh_data.get("global") or {}
    bbox_dims = global_stats.get("bbox_dimensions")

    if isinstance(bbox_dims, Sequence) and not isinstance(bbox_dims, (str, bytes)):
        vals = []
        for i in range(min(3, len(bbox_dims))):
            v = _as_float(bbox_dims[i])
            if v is not None:
                vals.append(v)
        if vals:
            return max(vals)

    bbox = metadata.get("bbox")
    if isinstance(bbox, Mapping):
        xmin = _as_float(bbox.get("xmin"))
        xmax = _as_float(bbox.get("xmax"))
        ymin = _as_float(bbox.get("ymin"))
        ymax = _as_float(bbox.get("ymax"))
        zmin = _as_float(bbox.get("zmin"))
        zmax = _as_float(bbox.get("zmax"))
        spans = []
        if xmin is not None and xmax is not None:
            spans.append(max(0.0, xmax - xmin))
        if ymin is not None and ymax is not None:
            spans.append(max(0.0, ymax - ymin))
        if zmin is not None and zmax is not None:
            spans.append(max(0.0, zmax - zmin))
        if spans:
            return max(spans)

    return None


def apply_auto_scale(features_data: Mapping[str, Any], process_cfg: Mapping[str, Any]) -> Dict[str, Any]:
    process_cfg = deep_merge(process_cfg)
    auto = process_cfg.get("auto_scale")
    if not isinstance(auto, Mapping) or not auto.get("enabled"):
        return process_cfg

    max_span = _max_span_from_features_data(features_data)
    if max_span is None:
        return process_cfg

    span_min = _as_float(auto.get("span_min_mm"))
    span_max = _as_float(auto.get("span_max_mm"))
    if span_min is None or span_max is None:
        return process_cfg

    if span_max <= span_min:
        t = 0.0
    else:
        t = _clamp01((max_span - span_min) / (span_max - span_min))

    for section_name in ("deposition", "path", "finishing"):
        base_section = process_cfg.get(section_name)
        scale_section = auto.get(section_name)
        if not isinstance(base_section, Mapping) or not isinstance(scale_section, Mapping):
            continue
        ranges = scale_section.get("ranges")
        if not isinstance(ranges, Mapping):
            continue

        scaled = dict(base_section)
        for key, entry in ranges.items():
            if not isinstance(entry, Mapping):
                continue
            vmin = _as_float(entry.get("at_min_span"))
            vmax = _as_float(entry.get("at_max_span"))
            if vmin is None or vmax is None:
                vmin = _as_float(entry.get("min"))
                vmax = _as_float(entry.get("max"))
            if vmin is None or vmax is None:
                continue
            scaled[key] = _lerp(vmin, vmax, t)
        process_cfg[section_name] = scaled

    process_cfg.setdefault("_auto_scale", {})
    process_cfg["_auto_scale"].update(
        {
            "enabled": True,
            "driver": auto.get("driver", "max_span"),
            "max_span_mm": max_span,
            "t": round(t, 6),
        }
    )
    return process_cfg


def normalize_waam_config(config_or_path: Optional[Any], features_data: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    if config_or_path is None:
        cfg: Dict[str, Any] = {}
    elif isinstance(config_or_path, str):
        cfg = _load_json(config_or_path)
    elif isinstance(config_or_path, Mapping):
        cfg = deep_merge(config_or_path)
    else:
        raise TypeError("config_or_path must be a mapping, path, or None")

    cfg.setdefault("version", 1)
    cfg.setdefault("units", "mm")
    cfg.setdefault("wcs", "G54")
    cfg.setdefault("metadata", {})
    cfg.setdefault("material", {})
    cfg.setdefault("machine", {})
    cfg.setdefault("quality", {})
    cfg.setdefault("safety", {})
    cfg.setdefault("heights", {})
    cfg.setdefault("defaults", {})
    cfg.setdefault("post", {})
    cfg.setdefault("validation", {})

    process = deep_merge(
        {
            "deposition": {
                "strategy": "contour_parallel",
                "layer_height_mm": 1.2,
                "bead_width_mm": 6.0,
                "overlap_pct": 30.0,
                "travel_speed_mm_min": 320.0,
                "wire_feed_mm_min": 3800.0,
                "arc_current_a": 240.0,
                "arc_voltage_v": 26.0,
                "start_end_overlap_mm": 5.0,
                "ramp_in_mm": 3.0,
                "ramp_out_mm": 3.0,
                "dwell_per_layer_s": 0.0,
                "support_trim": {"enabled": False, "radius_scale": 1.0},
            },
            "finishing": {
                "passes": 0,
            },
            "interpass": {
                "cooling": "none",
                "max_dwell_s": 0.0,
                "adaptive_dwell": False,
            },
            "path": {
                "planner_mode": "layered_topology",
                "infill_strategy": "zigzag",
                "start_strategy": "inside_out",
                "end_strategy": "none",
                "direction": "climb",
                "lead_in_type": "none",
                "lead_in_radius_mm": 0.0,
                "lead_out_type": "none",
                "lead_out_radius_mm": 0.0,
                "corner_strategy": "sharp",
                "min_corner_radius_mm": 0.0,
                "layer_sort": "risk_area",
                "contours_first": True,
                "contour_passes": 1,
                "alternate_direction": True,
                "angle_deg": 0.0,
                "angle_step_deg": 90.0,
                "min_planar_span_mm": None,
                "topology_sensitive": {
                    "enabled": True,
                    "void_lookahead_layers": 2,
                    "thin_wall_area_ratio_max": 0.45,
                    "suppress_infill_on_transition": True,
                    "thin_wall_detection": {
                        "enabled": True,
                        "use_mesh_signal": True,
                        "sample_step_mm": 1.5,
                        "max_samples_per_region": 96,
                        "single_track_max_width_beads": 1.35,
                        "dual_track_max_width_beads": 2.35,
                    },
                    "band_fill": {
                        "enabled": True,
                        "min_avg_width_beads": 2.0,
                        "min_p10_width_beads": 1.4,
                        "min_span_beads": 1.6,
                    },
                    "hotspots": {
                        "corner_angle_deg": 45.0,
                        "corner_clearance_mm": 8.0,
                        "junction_clearance_mm": 10.0,
                        "min_neck_width_beads": 2.0,
                        "suppress_infill_near_hotspots": True,
                    },
                    "continuity": {
                        "enabled": True,
                        "rotate_closed_paths": True,
                        "allow_reverse_open_paths": True,
                        "restart_penalty_mm": 20.0,
                        "heading_change_penalty_mm": 4.0,
                        "region_switch_penalty_mm": 3.0,
                        "max_chain_gap_mm": 0.8,
                        "emit_chains": False,
                        "allowed_roles": ["INFILL"],
                        "allowed_region_modes": ["bulk_fill", "band_fill"],
                        "disallow_hotspot_kinds": ["corner", "junction"],
                        "max_chain_heading_delta_deg": 60.0,
                        "max_paths_per_chain": 12,
                        "coincident_endpoint_tol_mm": 0.05,
                    },
                },
            },
            "auto_scale": {"enabled": False},
            "thermal": {},
        },
        cfg.get("process") or {},
    )

    if features_data is not None:
        process = apply_auto_scale(features_data, process)

    cfg["process"] = process

    cfg["heights"] = deep_merge(
        {
            "safe": 50.0,
            "clearance": 30.0,
            "rapidZ": 5.0,
            "arc_start_clearance": 3.0,
            "substrate_offset": 0.0,
        },
        cfg.get("heights") or {},
    )

    cfg["post"] = deep_merge(
        {
            "dialect": "linuxcnc",
            "emit_comments": True,
            "torch_on": "M62 P0",
            "torch_off": "M63 P0",
            "gas_on": "M64 P1",
            "gas_off": "M65 P1",
            "wire_feed": "M68 E0 Q{wire_feed:.0f}",
            "travel_speed": "F{travel_speed:.0f}",
            "interpass_pause": "G4 P{interpass_dwell:.1f}",
            "arc_start_delay_s": 0.0,
            "arc_stop_delay_s": 0.0,
            "gas_preflow_s": 0.0,
            "gas_postflow_s": 0.0,
        },
        cfg.get("post") or {},
    )

    cfg["validation"] = deep_merge(
        {
            "required_fields": [],
            "thermal_limits": {},
            "geometry_checks": {
                "min_wall_thickness_mm": 0.0,
                "max_overhang_angle_deg": 90.0,
                "min_feature_size_mm": 0.0,
            },
        },
        cfg.get("validation") or {},
    )

    tools = cfg.get("tools")
    if not isinstance(tools, list) or not tools:
        dep = cfg["process"]["deposition"]
        fin = cfg["process"].get("finishing") or {}
        tools = [
            {
                "name": "Torch_Primary",
                "type": "deposition",
                "diameter": dep.get("bead_width_mm", 6.0),
                "wire_feed_mm_min": dep.get("wire_feed_mm_min", 3800.0),
                "travel_speed_mm_min": dep.get("travel_speed_mm_min", 320.0),
                "arc_current_a": dep.get("arc_current_a", 240.0),
                "arc_voltage_v": dep.get("arc_voltage_v", 26.0),
                "feed": dep.get("travel_speed_mm_min", 320.0),
                "rpm": 0.0,
            },
            {
                "name": "Torch_Finish",
                "type": "finish",
                "diameter": fin.get("bead_width_mm", dep.get("bead_width_mm", 6.0)),
                "wire_feed_mm_min": fin.get("wire_feed_mm_min", dep.get("wire_feed_mm_min", 2800.0)),
                "travel_speed_mm_min": fin.get("travel_speed_mm_min", dep.get("travel_speed_mm_min", 420.0)),
                "arc_current_a": fin.get("arc_current_a", dep.get("arc_current_a", 180.0)),
                "arc_voltage_v": fin.get("arc_voltage_v", dep.get("arc_voltage_v", 24.0)),
                "feed": fin.get("travel_speed_mm_min", dep.get("travel_speed_mm_min", 420.0)),
                "rpm": 0.0,
            },
        ]
    cfg["tools"] = tools

    return cfg


def _feature_bbox(feature: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    bbox = feature.get("bbox")
    if isinstance(bbox, Mapping):
        xmin = _as_float(bbox.get("xmin"))
        xmax = _as_float(bbox.get("xmax"))
        ymin = _as_float(bbox.get("ymin"))
        ymax = _as_float(bbox.get("ymax"))
        zmin = _as_float(bbox.get("zmin"))
        zmax = _as_float(bbox.get("zmax"))
        if None not in (xmin, xmax, ymin, ymax):
            return {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax, "zmin": zmin or 0.0, "zmax": zmax or 0.0}
    elif isinstance(bbox, Sequence) and not isinstance(bbox, (str, bytes)):
        vals = []
        for item in bbox:
            vals.append(_as_float(item))
        if len(vals) >= 4 and None not in vals[:4]:
            zmin = vals[4] if len(vals) > 4 and vals[4] is not None else 0.0
            zmax = vals[5] if len(vals) > 5 and vals[5] is not None else zmin
            return {"xmin": vals[0], "xmax": vals[1], "ymin": vals[2], "ymax": vals[3], "zmin": zmin, "zmax": zmax}
    return None


def validate_features(features_data: Mapping[str, Any], config: Mapping[str, Any]) -> Dict[str, Any]:
    validation_cfg = (config or {}).get("validation") or {}
    geom = validation_cfg.get("geometry_checks") or {}
    min_feature_size = _as_float(geom.get("min_feature_size_mm")) or 0.0
    min_wall = _as_float(geom.get("min_wall_thickness_mm")) or 0.0
    max_overhang_angle = _as_float(geom.get("max_overhang_angle_deg"))

    warnings = []
    feature_flags: Dict[str, list[str]] = {}

    for feature in (features_data.get("features") or []):
        feat_id = str(feature.get("id") or "")
        if not feat_id:
            continue
        flags: list[str] = []

        bbox = _feature_bbox(feature)
        if bbox:
            span_x = max(0.0, bbox["xmax"] - bbox["xmin"])
            span_y = max(0.0, bbox["ymax"] - bbox["ymin"])
            min_span = min(span_x, span_y)
            if min_feature_size > 0 and min_span > 0 and min_span < min_feature_size:
                flags.append(f"feature smaller than min_feature_size_mm ({min_span:.3f} < {min_feature_size:.3f})")
            if feature.get("type") == "planar_pocket_floor" and min_wall > 0 and min_span > 0 and min_span < min_wall:
                flags.append(f"planar region thinner than min_wall_thickness_mm ({min_span:.3f} < {min_wall:.3f})")

        risk_tags = set(feature.get("risk_tags") or [])
        if max_overhang_angle is not None and "overhang_region" in risk_tags:
            flags.append(f"overhang-region present; review against max_overhang_angle_deg={max_overhang_angle:.1f}")
        if feature.get("type") == "freeform_area":
            flags.append("freeform_area uses bbox fallback; volumetric slicing refinement recommended")

        if flags:
            feature_flags[feat_id] = flags
            for msg in flags:
                warnings.append({"feature_id": feat_id, "level": "warning", "message": msg})

    thermal_limits = validation_cfg.get("thermal_limits") or {}
    if thermal_limits.get("forced_cooling_interval"):
        warnings.append(
            {
                "feature_id": None,
                "level": "info",
                "message": f"forced_cooling_interval={thermal_limits['forced_cooling_interval']} is available for downstream post/adaptive dwell logic",
            }
        )

    return {
        "ok": not any(w["level"] == "warning" for w in warnings),
        "warnings": warnings,
        "feature_flags": feature_flags,
        "rules": validation_cfg,
    }
