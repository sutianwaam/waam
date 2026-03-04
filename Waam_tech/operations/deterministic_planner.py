from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Tuple


_RISK_WEIGHTS = {
    "overhang_region": 3,
    "overhang_neighbor": 2,
    "thin_wall_neighbor": 2,
    "thin_wall": 2,
}


def _build_tool_index(tools: Iterable[Dict]) -> Dict[str, Dict]:
    """
    Build quick lookup dictionaries for the available tooling.
    Returns a map keyed by both tool `name` and `type` so sequencing logic can
    grab the first suitable candidate without hard-coded identifiers.
    """
    by_key: Dict[str, Dict] = {}
    for tool in tools:
        name = tool.get("name")
        ttype = tool.get("type")
        if name and name not in by_key:
            by_key[name] = tool
        # prefer first tool of a given type as the default representative
        type_key = f"type::{ttype}"
        if ttype and type_key not in by_key:
            by_key[type_key] = tool
    return by_key


def _pick_tool(tool_index: Dict[str, Dict], *candidates: str) -> Optional[str]:
    """
    Resolve the first matching tool name from the candidate list.
    Candidates can be explicit tool names (e.g. 'T1') or semantic tags such as
    'type::flat'. The function returns the chosen tool's `name`, or None when
    nothing matches.
    """
    for candidate in candidates:
        tool = tool_index.get(candidate)
        if tool:
            return tool.get("name")
    return None


def _get_default(mapping: Dict, key: str, fallback):
    value = mapping.get(key)
    return fallback if value is None else value


def _as_optional_float(value) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _lerp(start: float, end: float, t: float) -> float:
    return start + (end - start) * t


def _planar_bbox_span(feature: Dict) -> Optional[Tuple[float, float]]:
    bbox = feature.get("bbox")
    if isinstance(bbox, dict):
        try:
            xmin = float(bbox["xmin"])
            xmax = float(bbox["xmax"])
            ymin = float(bbox["ymin"])
            ymax = float(bbox["ymax"])
        except Exception:
            return None
    elif isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            xmin, xmax, ymin, ymax = (float(bbox[i]) for i in range(4))
        except Exception:
            return None
    else:
        return None
    return max(0.0, xmax - xmin), max(0.0, ymax - ymin)


def _filter_small_planar_faces(features: Iterable[Dict], min_span_mm: float) -> Tuple[List[Dict], List[str]]:
    if min_span_mm <= 0:
        return list(features), []
    kept: List[Dict] = []
    dropped: List[str] = []
    for feat in features:
        if feat.get("type") == "planar_pocket_floor":
            span = _planar_bbox_span(feat)
            if span is not None and min(span) < min_span_mm:
                feat_id = feat.get("id")
                if feat_id:
                    dropped.append(str(feat_id))
                continue
        kept.append(feat)
    return kept, dropped


def _max_span_from_features(features_json: Mapping) -> Optional[float]:
    metadata = features_json.get("metadata", {}) if isinstance(features_json, Mapping) else {}
    mesh_data = metadata.get("mesh_analysis") or metadata.get("mesh") or {}
    global_stats = mesh_data.get("global") or {}
    bbox_dims = global_stats.get("bbox_dimensions")
    if isinstance(bbox_dims, (list, tuple)) and bbox_dims:
        try:
            span_vals = [float(bbox_dims[i]) for i in range(min(3, len(bbox_dims)))]
            if span_vals:
                return max(span_vals)
        except Exception:
            pass

    bbox = metadata.get("bbox")
    if isinstance(bbox, Mapping):
        try:
            x_len = float(bbox["xmax"]) - float(bbox["xmin"])
            y_len = float(bbox["ymax"]) - float(bbox["ymin"])
            z_len = float(bbox["zmax"]) - float(bbox["zmin"])
            return max(x_len, y_len, z_len)
        except Exception:
            return None
    return None


def _auto_scale_process(features_json: Mapping, process_cfg: Mapping) -> Dict:
    if not isinstance(process_cfg, Mapping):
        return {}
    auto = process_cfg.get("auto_scale")
    if not isinstance(auto, Mapping) or not auto.get("enabled"):
        return dict(process_cfg)

    max_span = _max_span_from_features(features_json)
    if max_span is None:
        return dict(process_cfg)

    span_min = _as_optional_float(auto.get("span_min_mm"))
    span_max = _as_optional_float(auto.get("span_max_mm"))
    if span_min is None or span_max is None:
        return dict(process_cfg)
    if span_max <= span_min:
        t = 0.0
    else:
        t = _clamp01((max_span - span_min) / (span_max - span_min))

    result = dict(process_cfg)
    for section, params in (
        (
            "deposition",
            (
                "bead_width_mm",
                "layer_height_mm",
                "travel_speed_mm_min",
                "wire_feed_mm_min",
                "overlap_pct",
                "ramp_in_mm",
                "ramp_out_mm",
            ),
        ),
        (
            "path",
            (
                "lead_in_radius_mm",
                "lead_out_radius_mm",
                "min_corner_radius_mm",
            ),
        ),
    ):
        base = process_cfg.get(section)
        if not isinstance(base, Mapping):
            continue
        scale_cfg = auto.get(section)
        if not isinstance(scale_cfg, Mapping):
            continue
        ranges = scale_cfg.get("ranges")
        if not isinstance(ranges, Mapping):
            continue
        scaled = dict(base)
        for key in params:
            entry = ranges.get(key)
            if not isinstance(entry, Mapping):
                continue
            vmin = _as_optional_float(entry.get("at_min_span"))
            vmax = _as_optional_float(entry.get("at_max_span"))
            if vmin is None or vmax is None:
                vmin = _as_optional_float(entry.get("min"))
                vmax = _as_optional_float(entry.get("max"))
            if vmin is None or vmax is None:
                continue
            scaled[key] = _lerp(vmin, vmax, t)
        result[section] = scaled
    return result


def _mesh_operation_context(features_data: Dict) -> Dict[str, float | bool]:
    """
    Derive heuristic planning hints from mesh analysis data embedded in features.json.
    """
    metadata = features_data.get("metadata", {})
    mesh_data = metadata.get("mesh_analysis") or metadata.get("mesh") or {}
    global_stats = mesh_data.get("global") or {}
    bbox = global_stats.get("bbox_dimensions") or [0.0, 0.0, 0.0]
    try:
        span_x, span_y, span_z = (float(bbox[i]) if i < len(bbox) else 0.0 for i in range(3))
    except Exception:
        span_x = span_y = span_z = 0.0
    max_span = max(span_x, span_y, span_z, 1.0)
    thin_walls = mesh_data.get("thin_walls") or {}
    thin_wall_risk = bool(thin_walls.get("facet_count", 0))
    overhangs = mesh_data.get("overhangs") or {}
    overhang_cov = float(overhangs.get("coverage_pct") or 0.0)
    fine_detail = thin_wall_risk or overhang_cov >= 5.0

    rough_stepdown = max(0.6, min(4.0, span_z / 25.0 if span_z else 1.5))
    finish_stepdown = max(0.4, min(2.5, span_z / 35.0 if span_z else 0.9))
    finish_stepover_pct = 0.25 if fine_detail else 0.4

    return {
        "rough_stepdown": rough_stepdown,
        "rough_stock": 0.6 if thin_wall_risk else 0.0,
        "finish_enabled": fine_detail or span_z <= 60.0,
        "finish_stepdown": finish_stepdown,
        "finish_stepover_pct": finish_stepover_pct,
        "finish_allowance": 0.35 if thin_wall_risk else 0.0,
        "finish_pocket_enabled": thin_wall_risk,
        "max_span": max_span,
    }

def _feature_layer_info(feature: Dict, layer_height: float) -> Dict[str, float | int]:
    """
    Estimate a layer index/z-height for a feature so downstream consumers can
    reason about build order without re-reading the original mesh data.
    """
    def _as_float(value) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None

    candidates = [
        _as_float(feature.get("z_top")),
        _as_float(feature.get("z_top_mm")),
    ]
    bbox = feature.get("bbox")
    if isinstance(bbox, dict):
        candidates.append(_as_float(bbox.get("zmax")))
    elif isinstance(bbox, (list, tuple)) and len(bbox) >= 2:
        max_corner = bbox[1]
        if isinstance(max_corner, (list, tuple)) and len(max_corner) >= 3:
            candidates.append(_as_float(max_corner[2]))
    center = feature.get("center")
    if isinstance(center, (list, tuple)) and len(center) == 3:
        candidates.append(_as_float(center[2]))

    z_val = next((val for val in candidates if val is not None), 0.0)
    normalized_height = layer_height if layer_height and layer_height > 0 else 1.0
    layer_index = int(round(z_val / normalized_height)) if normalized_height else 0
    return {"index": layer_index, "z_mm": z_val}


def _summarize_feature(feature: Dict, layer_height: float) -> Dict:
    """
    Build a compact, LLM-friendly snapshot of each feature that includes
    geometry cues, risk tags, and layer placement.
    """
    summary = {
        "id": feature.get("id"),
        "type": feature.get("type"),
        "owner": feature.get("owner"),
        "risk_tags": feature.get("risk_tags") or [],
    }
    optional_fields = (
        "center",
        "axis_wcs",
        "bbox",
        "diameter",
        "depth",
        "width",
        "length",
        "height",
        "area",
        "volume",
        "z_top",
        "z_bottom",
        "thickness",
        "entry_point",
        "exit_point",
        "orientation",
    )
    for key in optional_fields:
        value = feature.get(key)
        if value not in (None, [], {}):
            summary[key] = value

    summary["layer"] = _feature_layer_info(feature, layer_height)
    return summary


def _process_profile_for_operation(op_name: str) -> str:
    if op_name.startswith("finish"):
        return "finishing"
    if op_name.startswith("drill"):
        return "drilling"
    return "deposition"


def _risk_score(tags: Iterable[str] | None) -> int:
    if not tags:
        return 0
    score = 0
    for tag in tags:
        if not tag:
            continue
        score += _RISK_WEIGHTS.get(str(tag), 1)
    return score


def _feature_footprint_area(feature: Mapping | None) -> float:
    if not isinstance(feature, Mapping):
        return 0.0
    for key in ("area_mm2", "area"):
        try:
            if key in feature and feature.get(key) is not None:
                return max(0.0, float(feature.get(key)))
        except Exception:
            pass
    bbox = feature.get("bbox")
    try:
        if isinstance(bbox, Mapping):
            xmin = float(bbox["xmin"])
            xmax = float(bbox["xmax"])
            ymin = float(bbox["ymin"])
            ymax = float(bbox["ymax"])
            return max(0.0, (xmax - xmin) * (ymax - ymin))
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            xmin, xmax, ymin, ymax = (float(bbox[i]) for i in range(4))
            return max(0.0, (xmax - xmin) * (ymax - ymin))
    except Exception:
        return 0.0
    return 0.0


def _op_priority(op_name: str) -> int:
    op = (op_name or "").lower()
    if op.startswith("rough"):
        return 0
    if op.startswith("finish"):
        return 1
    if op.startswith("drill"):
        return 2
    return 3


def _reorder_sequence_within_layers(
    sequence: List[Dict],
    feature_index: Mapping[str, Mapping],
    feature_map: Mapping[str, Mapping],
    *,
    strategy: str,
) -> List[Dict]:
    if not sequence:
        return sequence
    normalized = (strategy or "").strip().lower()
    if normalized in {"", "none", "off", "disabled"}:
        return sequence
    if normalized not in {"risk_area", "risk_area_desc"}:
        return sequence

    def _risk_key(score: int) -> int:
        return -score if normalized == "risk_area_desc" else score

    layer_entries: Dict[int, Dict[str, object]] = {}
    unassigned: List[Tuple[int, Dict]] = []

    for idx, entry in enumerate(sequence):
        feat_id = entry.get("region") or entry.get("feature_id")
        if not feat_id:
            unassigned.append((idx, entry))
            continue
        feat_id = str(feat_id)
        summary = feature_index.get(feat_id) or {}
        layer = summary.get("layer") or {}
        layer_idx = layer.get("index")
        if layer_idx is None:
            unassigned.append((idx, entry))
            continue
        try:
            layer_idx = int(layer_idx)
        except Exception:
            unassigned.append((idx, entry))
            continue
        layer_z = layer.get("z_mm")
        try:
            layer_z = float(layer_z) if layer_z is not None else None
        except Exception:
            layer_z = None

        risk = _risk_score(summary.get("risk_tags"))
        area = _feature_footprint_area(feature_map.get(feat_id))
        op_pri = _op_priority(entry.get("op", ""))

        bucket = layer_entries.setdefault(
            layer_idx,
            {"z": layer_z, "items": []},
        )
        if bucket["z"] is None and layer_z is not None:
            bucket["z"] = layer_z
        bucket["items"].append((risk, area, op_pri, idx, entry))

    ordered_layers = sorted(
        layer_entries.items(),
        key=lambda item: (
            float(item[1].get("z")) if item[1].get("z") is not None else 0.0,
            item[0],
        ),
    )

    reordered: List[Dict] = []
    for _, payload in ordered_layers:
        items = payload.get("items", [])
        items_sorted = sorted(
            items,
            key=lambda item: (_risk_key(item[0]), -float(item[1]), item[2], item[3]),
        )
        reordered.extend(item[-1] for item in items_sorted)

    if unassigned:
        reordered.extend(entry for _, entry in sorted(unassigned, key=lambda item: item[0]))
    return reordered


def generate_operation_sequence(
    features_data: Dict,
    tools_available: List[Dict],
    op_defaults: Optional[Dict] = None,
) -> List[Dict]:
    """
    Create a deterministic machining sequence from the extracted features.
    This forms the baseline plan before the LLM/user review loop tweaks feeds,
    tooling, or strategy parameters.
    """
    tool_index = _build_tool_index(tools_available)
    op_defaults = op_defaults or {}

    rough_defaults = op_defaults.get("roughing", {})
    finish_defaults = op_defaults.get("finishing", {})
    finish_pocket_defaults = op_defaults.get("finish_pocket", {})
    drill_defaults = op_defaults.get("drill", {})

    mesh_ctx = _mesh_operation_context(features_data)

    rough_enabled = bool(_get_default(rough_defaults, "enabled", True))
    finish_enabled = bool(_get_default(finish_defaults, "enabled", mesh_ctx["finish_enabled"]))
    finish_pocket_enabled = bool(_get_default(finish_pocket_defaults, "enabled", mesh_ctx["finish_pocket_enabled"]))
    drill_enabled = bool(_get_default(drill_defaults, "enabled", False))

    pockets = [f for f in features_data.get("features", []) if "pocket" in f.get("type", "")]
    holes = [f for f in features_data.get("features", []) if "hole" in f.get("type", "")]
    freeforms = [f for f in features_data.get("features", []) if f.get("type") == "freeform_area"]

    sequence: List[Dict] = []

    # 1. Rough pockets (shallower Z first so we clear top-down stock)
    pocket_tool = None
    if rough_enabled:
        pocket_tool = _pick_tool(
            tool_index,
            "Torch_Primary",
            "type::deposition",
            "T1",
            "type::flat",
        )
    if rough_enabled and pocket_tool:
        stepdown = float(_get_default(rough_defaults, "stepdown", mesh_ctx["rough_stepdown"]))
        stock_to_leave = float(_get_default(rough_defaults, "stock_to_leave", mesh_ctx["rough_stock"]))
        for pocket in sorted(pockets, key=lambda p: p.get("z", 0.0)):
            sequence.append({
                "op": "rough_pocket",
                "tool": pocket_tool,
                "region": pocket["id"],
                "params": {
                    "stepdown": stepdown,
                    "stockToLeave": stock_to_leave,
                    "feeds": {
                        "xy": float(_get_default(rough_defaults, "feed_xy", 320.0)),
                        "z": float(_get_default(rough_defaults, "feed_z", 200.0)),
                    },
                    "rpm_cap": float(_get_default(rough_defaults, "rpm_cap", 0.0)),
                }
            })

    # 2. Freeform finishing regions
    finish_surface_tool = None
    if finish_enabled:
        finish_surface_tool = _pick_tool(
            tool_index,
            "Torch_Finish",
            "type::finish",
            "T2",
            "type::ball",
        )
    if finish_enabled and finish_surface_tool:
        finish_tool_data = tool_index.get(finish_surface_tool, {})
        diameter = float(finish_tool_data.get("diameter", 1.0))
        stepover_pct = float(_get_default(finish_defaults, "stepover_pct", mesh_ctx["finish_stepover_pct"]))
        stepover = max(0.1, stepover_pct * diameter)
        finish_stepdown = float(_get_default(finish_defaults, "stepdown", mesh_ctx["finish_stepdown"]))
        for area in freeforms:
            sequence.append({
                "op": "finish_surface",
                "tool": finish_surface_tool,
                "region": area["id"],
                "params": {
                    "stepover": stepover,
                    "stepdown": finish_stepdown,
                    "stockToLeave": float(_get_default(finish_defaults, "stock_to_leave", mesh_ctx["finish_allowance"])),
                    "feeds": {
                        "xy": float(_get_default(finish_defaults, "feed_xy", 420.0)),
                        "z": float(_get_default(finish_defaults, "feed_z", 200.0)),
                    },
                    "rpm_cap": float(_get_default(finish_defaults, "rpm_cap", 0.0)),
                }
            })

    # 3. Group drilling targets into a single cycle
    drill_tool = None
    if drill_enabled:
        drill_tool = _pick_tool(tool_index, "T3", "type::drill")
    if drill_enabled and drill_tool and holes:
        max_depth = max(h.get("depth", 0.0) for h in holes)
        peck_depth = float(drill_defaults.get("peck_depth", 0.0))
        sequence.append({
            "op": "drill",
            "tool": drill_tool,
            "params": {
                "targets": [h["id"] for h in holes],
                "peck": peck_depth > 0.0 and max_depth > peck_depth,
                "cycle": drill_defaults.get("cycle", "G82"),
                "rpm": float(_get_default(drill_defaults, "rpm", 0.0)),
                "feed": float(_get_default(drill_defaults, "feed", 0.0)),
                "peck_depth": peck_depth if peck_depth > 0.0 else None,
            }
            })

    # 4. Optional finish passes for pockets
    finish_pocket_tool = None
    if finish_pocket_enabled:
        finish_pocket_tool = _pick_tool(
            tool_index,
            "Torch_Finish",
            pocket_tool,
            finish_surface_tool,
            "type::finish",
            "type::ball",
        )
    if finish_pocket_enabled and finish_pocket_tool:
        for pocket in pockets:
            sequence.append({
                "op": "finish_pocket",
                "tool": finish_pocket_tool,
                "region": pocket["id"],
                "params": {
                    "allowance": float(
                        _get_default(
                            finish_pocket_defaults,
                            "allowance",
                            _get_default(finish_defaults, "stock_to_leave", mesh_ctx["finish_allowance"]),
                        )
                    ),
                    "feeds": {
                        "xy": float(
                            _get_default(
                                finish_pocket_defaults,
                                "feed_xy",
                                _get_default(finish_defaults, "feed_xy", 420.0),
                            )
                        ),
                        "z": float(
                            _get_default(
                                finish_pocket_defaults,
                                "feed_z",
                                _get_default(finish_defaults, "feed_z", 200.0),
                            )
                        ),
                    }
                }
            })

    return sequence


def plan_ops(features_json, config=None, tools=None, machine=None):
    config = config or {}
    config_tools = config.get("tools") or []
    heights = config.get("heights", {})
    process_cfg = config.get("process") or {}
    process_cfg = _auto_scale_process(features_json, process_cfg)
    deposition_layer_height = (
        process_cfg.get("deposition", {}).get("layer_height_mm")
        or process_cfg.get("deposition", {}).get("layer_height")
        or 1.0
    )
    bead_width = (
        process_cfg.get("deposition", {}).get("bead_width_mm")
        or process_cfg.get("deposition", {}).get("bead_width")
        or 6.0
    )
    min_span_cfg = _as_optional_float((process_cfg.get("path") or {}).get("min_planar_span_mm"))
    min_planar_span_mm = float(min_span_cfg) if min_span_cfg is not None else float(bead_width)

    tools = tools or config_tools or [
        {
            "name": "Torch_Primary",
            "type": "deposition",
            "diameter": 6.0,
            "rpm": 0.0,
            "feed": 320.0,
            "wire_feed_mm_min": 3800.0,
            "arc_current_a": 240.0,
            "arc_voltage_v": 26.0,
        },
        {
            "name": "Torch_Finish",
            "type": "finish",
            "diameter": 4.5,
            "rpm": 0.0,
            "feed": 420.0,
            "wire_feed_mm_min": 2800.0,
            "arc_current_a": 180.0,
            "arc_voltage_v": 24.0,
        },
    ]
    machine = machine or {
        "clearance": float(heights.get("clearance", 15.0)),
        "rapidZ": float(heights.get("rapidZ", 2.0)),
    }

    features = features_json.get("features") or []
    filtered_features, dropped_planar = _filter_small_planar_faces(features, min_planar_span_mm)
    features_data = dict(features_json)
    features_data["features"] = filtered_features
    feature_map: Dict[str, Dict] = {}
    for feature in filtered_features:
        feature_id = feature.get("id")
        if feature_id:
            feature_map[str(feature_id)] = feature
    feature_index = {}
    for feature in filtered_features:
        feature_id = feature.get("id")
        if not feature_id:
            continue
        feature_index[feature_id] = _summarize_feature(feature, deposition_layer_height)

    sequence = generate_operation_sequence(
        features_data,
        tools,
        op_defaults=config.get("defaults"),
    )
    layer_sort = (process_cfg.get("path") or {}).get("layer_sort", "risk_area")
    sequence = _reorder_sequence_within_layers(
        sequence,
        feature_index,
        feature_map,
        strategy=str(layer_sort),
    )
    mesh_ctx = _mesh_operation_context(features_json)

    layer_buckets: Dict[int, Dict] = {}
    for idx, entry in enumerate(sequence):
        entry["sequence_index"] = idx
        op_name = entry.get("op", "")
        entry["process_profile"] = _process_profile_for_operation(op_name)
        feature_id = entry.get("region")
        if feature_id and feature_id in feature_index:
            feature_summary = feature_index[feature_id]
            entry["feature_id"] = feature_id
            entry["feature_type"] = feature_summary.get("type")
            entry["feature_tags"] = feature_summary.get("risk_tags", [])
            layer = feature_summary.get("layer")
            if layer:
                entry["layer_index"] = layer.get("index")
                entry["layer_z_mm"] = layer.get("z_mm")
                bucket = layer_buckets.setdefault(
                    layer["index"],
                    {
                        "index": layer["index"],
                        "z_mm": layer["z_mm"],
                        "features": [],
                        "operations": [],
                    },
                )
                if feature_id not in bucket["features"]:
                    bucket["features"].append(feature_id)
                bucket["operations"].append(
                    {
                        "op": op_name,
                        "sequence_index": idx,
                        "feature_id": feature_id,
                    }
                )
        elif op_name == "drill":
            targets = entry.get("params", {}).get("targets") or []
            entry["feature_targets"] = targets
            for target in targets:
                feature_summary = feature_index.get(target)
                if not feature_summary:
                    continue
                layer = feature_summary.get("layer")
                if not layer:
                    continue
                bucket = layer_buckets.setdefault(
                    layer["index"],
                    {
                        "index": layer["index"],
                        "z_mm": layer["z_mm"],
                        "features": [],
                        "operations": [],
                    },
                )
                if target not in bucket["features"]:
                    bucket["features"].append(target)
                bucket["operations"].append(
                    {
                        "op": op_name,
                        "sequence_index": idx,
                        "feature_id": target,
                    }
                )

    layers = sorted(layer_buckets.values(), key=lambda item: (item["z_mm"], item["index"]))

    process_parameters = {
        "deposition": process_cfg.get("deposition"),
        "finishing": process_cfg.get("finishing"),
        "interpass": process_cfg.get("interpass"),
        "path": process_cfg.get("path"),
    }

    metadata = {
        "units": features_json.get("metadata", {}).get("units", "mm"),
        "wcs": features_json.get("metadata", {}).get("wcs"),
        "clearance": machine["clearance"],
        "rapidZ": machine["rapidZ"],
        "config_version": config.get("version"),
        "machine": config.get("machine"),
        "post": config.get("post"),
        "mesh_strategy": mesh_ctx,
        "mesh_analysis": features_json.get("metadata", {}).get("mesh_analysis"),
    }
    if dropped_planar:
        metadata["filters"] = {
            "min_planar_span_mm": min_planar_span_mm,
            "dropped_planar_faces": dropped_planar,
        }
    deposition_start = features_json.get("metadata", {}).get("deposition_start")
    if deposition_start:
        metadata["deposition_start"] = deposition_start

    result = {
        "metadata": metadata,
        "tools": tools,
        "sequence": sequence,
        "feature_index": feature_index,
        "layers": layers,
        "process_parameters": process_parameters,
        "material": config.get("material"),
        "quality": config.get("quality"),
        "safety": config.get("safety"),
    }
    validation = config.get("validation")
    if validation:
        result["validation"] = validation

    snippets = config.get("snippets")
    if snippets:
        result["snippets"] = snippets

    return result
