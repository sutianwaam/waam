from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping, Optional


_DEPOSITION_RESLICE_KEYS = {
    "layer_height_mm",
    "bead_width_mm",
    "overlap_pct",
    "strategy",
}
_PATH_RESLICE_KEYS = {
    "deposition_pattern",
    "hatch_angle_deg",
    "hatch_angle_step_deg",
    "angle_deg",
    "angle_step_deg",
}


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def build_reslice_config_override(
    config_override: Optional[Mapping[str, object]] = None,
    *,
    layer_height_mm: Optional[float] = None,
    bead_width_mm: Optional[float] = None,
    overlap_pct: Optional[float] = None,
    deposition_pattern: Optional[str] = None,
) -> dict[str, object]:
    override = deepcopy(dict(config_override or {}))
    if layer_height_mm is None and bead_width_mm is None and overlap_pct is None and deposition_pattern is None:
        return override

    process = dict(_as_mapping(override.get("process")))
    deposition = dict(_as_mapping(process.get("deposition")))
    path = dict(_as_mapping(process.get("path")))

    if layer_height_mm is not None:
        deposition["layer_height_mm"] = float(layer_height_mm)
    if bead_width_mm is not None:
        deposition["bead_width_mm"] = float(bead_width_mm)
    if overlap_pct is not None:
        deposition["overlap_pct"] = float(overlap_pct)
    if deposition_pattern is not None:
        path["deposition_pattern"] = str(deposition_pattern).strip()

    if deposition:
        process["deposition"] = deposition
    if path:
        process["path"] = path
    override["process"] = process
    return override


def reslice_override_keys(config_override: Optional[Mapping[str, object]]) -> list[str]:
    override = _as_mapping(config_override)
    process = _as_mapping(override.get("process"))
    deposition = _as_mapping(process.get("deposition"))
    path = _as_mapping(process.get("path"))
    auto_scale = _as_mapping(process.get("auto_scale"))

    keys: list[str] = []
    keys.extend(
        f"process.deposition.{key}"
        for key in sorted(_DEPOSITION_RESLICE_KEYS)
        if key in deposition
    )
    keys.extend(
        f"process.path.{key}"
        for key in sorted(_PATH_RESLICE_KEYS)
        if key in path
    )
    if auto_scale and bool(auto_scale.get("enabled")):
        keys.append("process.auto_scale")
    return keys


def has_experimental_reslice_override(config_override: Optional[Mapping[str, object]]) -> bool:
    return bool(reslice_override_keys(config_override))


def _values_equivalent(a: object, b: object, *, tol: float = 1e-9) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def reslice_override_changes_plan(
    config_override: Optional[Mapping[str, object]],
    plan_path: Path | str,
) -> bool:
    override = _as_mapping(config_override)
    keys = reslice_override_keys(override)
    if not keys:
        return False

    try:
        plan_data = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except Exception:
        return True

    process = _as_mapping(plan_data.get("process") or plan_data.get("process_parameters"))
    plan_deposition = _as_mapping(process.get("deposition"))
    plan_path_cfg = _as_mapping(process.get("path"))
    override_process = _as_mapping(override.get("process"))
    override_deposition = _as_mapping(override_process.get("deposition"))
    override_path_cfg = _as_mapping(override_process.get("path"))
    override_auto_scale = _as_mapping(override_process.get("auto_scale"))
    metadata = _as_mapping(plan_data.get("metadata"))
    stage_manifest = _as_mapping(metadata.get("stage_manifest"))
    plan_auto_scale = _as_mapping(stage_manifest.get("auto_scale"))
    auto_scale_already_applied = bool(override_auto_scale.get("enabled")) and bool(
        plan_auto_scale.get("enabled")
    )

    for key in _DEPOSITION_RESLICE_KEYS:
        if auto_scale_already_applied and key in {"bead_width_mm", "layer_height_mm", "overlap_pct"}:
            continue
        if key in override_deposition and not _values_equivalent(
            override_deposition.get(key),
            plan_deposition.get(key),
        ):
            return True

    for key in _PATH_RESLICE_KEYS:
        if key not in override_path_cfg:
            continue
        plan_value = plan_path_cfg.get(key)
        if key == "deposition_pattern" and plan_value is None:
            plan_value = plan_data.get("deposition_pattern")
        if not _values_equivalent(override_path_cfg.get(key), plan_value):
            return True

    if override_auto_scale and bool(override_auto_scale.get("enabled")):
        if not bool(plan_auto_scale.get("enabled")):
            return True

    return False


def scratch_slice_plan_paths(
    workspace_root: Path | str,
    *,
    output_stem: str = "waam_slice_plan_resliced",
) -> dict[str, Path]:
    stem = str(output_stem or "waam_slice_plan_resliced").strip()
    if stem.endswith(".json"):
        stem = stem[:-5]
    scratch_root = Path(workspace_root).expanduser().resolve() / "scratch"
    return {
        "plan": scratch_root / f"{stem}.json",
        "features": scratch_root / f"{stem}_features.json",
        "llm_waam": scratch_root / f"{stem}.llm_waam",
    }


def _call_write_slice_plan_for_document(**kwargs: Any):
    from Waam_tech.slicing import write_slice_plan_for_document

    return write_slice_plan_for_document(**kwargs)


def write_scratch_slice_plan(
    context: Any,
    *,
    workspace_root: Path | str,
    config_override: Optional[Mapping[str, object]] = None,
    layer_height_mm: Optional[float] = None,
    bead_width_mm: Optional[float] = None,
    overlap_pct: Optional[float] = None,
    deposition_pattern: Optional[str] = None,
    output_stem: str = "waam_slice_plan_resliced",
):
    doc = getattr(context, "doc", None)
    objects = getattr(context, "objects", None)
    config = getattr(context, "config", None)
    if doc is None:
        raise RuntimeError("Experimental reslice requested, but context.doc is not available.")
    if not objects:
        raise RuntimeError("Experimental reslice requested, but context.objects is empty.")
    if not isinstance(config, Mapping):
        raise RuntimeError("Experimental reslice requested, but context.config is not available.")

    effective_override = build_reslice_config_override(
        config_override if config_override is not None else getattr(context, "config_override", None),
        layer_height_mm=layer_height_mm,
        bead_width_mm=bead_width_mm,
        overlap_pct=overlap_pct,
        deposition_pattern=deposition_pattern,
    )
    paths = scratch_slice_plan_paths(workspace_root, output_stem=output_stem)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    return _call_write_slice_plan_for_document(
        doc=doc,
        objects=list(objects),
        config=dict(config),
        output_path=paths["plan"],
        config_path=getattr(context, "config_path", None),
        config_override=effective_override,
        features_path=paths["features"],
        llm_waam_output_path=paths["llm_waam"],
        step_path=getattr(context, "step_path", None),
    )
