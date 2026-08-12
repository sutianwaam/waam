"""Fail-fast structural validation for waam_slice_plan.json before NC generation.

This is the checkpoint between stage A (slicing) and stage B (NC
generation): it does not re-derive geometry, it only checks that the plan
JSON has the shape the NC generator requires, and names the stage that most
likely produced bad data so a triage agent can route the failure correctly.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Sequence


class PlanSchemaError(ValueError):
    """Raised when a slice plan is missing required structure for NC generation."""


def _stage_prefix(problem: str) -> str:
    return f"[stage: slice_plan -> waamgen] {problem}"


def validate_plan_schema(data: Mapping[str, Any]) -> List[str]:
    """Validate required structure of a slice plan.

    Raises `PlanSchemaError` for problems that make NC generation impossible
    (missing metadata/WCS, no layers, no paths). Returns a list of
    non-fatal warnings (e.g. non-monotonic layer Z) that callers may log.
    """

    if not isinstance(data, Mapping):
        raise PlanSchemaError(_stage_prefix("Plan JSON must contain an object at the top level."))

    metadata = data.get("metadata")
    if not isinstance(metadata, Mapping):
        raise PlanSchemaError(_stage_prefix("Plan is missing a 'metadata' object."))

    units = metadata.get("units")
    if not isinstance(units, str) or not units.strip():
        raise PlanSchemaError(_stage_prefix("metadata.units is required and must be a non-empty string."))

    wcs_val = metadata.get("wcs")
    wcs_name = ""
    if isinstance(wcs_val, str):
        wcs_name = wcs_val.strip()
    elif isinstance(wcs_val, Mapping):
        wcs_name = str(wcs_val.get("name") or "").strip()
    if not wcs_name:
        raise PlanSchemaError(
            _stage_prefix(
                "metadata.wcs is missing or empty. This usually means slicing ran without a "
                "resolved WCS; re-run 'WAAM: Set WCS' before slicing."
            )
        )

    ctx = data.get("llm_context")
    if not isinstance(ctx, Mapping):
        raise PlanSchemaError(_stage_prefix("Plan is missing 'llm_context'."))

    structured = ctx.get("waam_structured") or ctx.get("toolpath")
    if not isinstance(structured, Mapping):
        raise PlanSchemaError(
            _stage_prefix("Plan is missing 'llm_context.waam_structured' (or legacy 'toolpath').")
        )

    paths = structured.get("paths")
    if not isinstance(paths, Mapping) or not paths:
        raise PlanSchemaError(
            _stage_prefix(
                "waam_structured.paths is empty. This usually means slicing produced no "
                "deposition paths (check support trimming / contour clipping)."
            )
        )

    layers = structured.get("layers")
    if not isinstance(layers, Sequence) or isinstance(layers, (str, bytes)) or not layers:
        raise PlanSchemaError(
            _stage_prefix(
                "waam_structured.layers is empty. This usually means slicing produced too few "
                "populated layers for the part height (check WCS orientation / bbox)."
            )
        )

    warnings: List[str] = []
    prev_z: float | None = None
    for item in layers:
        if not isinstance(item, Mapping):
            raise PlanSchemaError(_stage_prefix("Each entry in waam_structured.layers must be an object."))
        if "z" not in item and "z_mm" not in item:
            raise PlanSchemaError(_stage_prefix("A layer entry is missing 'z'."))
        path_ids = item.get("path_ids")
        if not isinstance(path_ids, Sequence) or isinstance(path_ids, (str, bytes)) or not path_ids:
            raise PlanSchemaError(_stage_prefix(f"Layer {item.get('i', item.get('index'))} has no path_ids."))
        try:
            z_val = float(item.get("z", item.get("z_mm")))
        except Exception:
            raise PlanSchemaError(_stage_prefix("A layer entry has a non-numeric 'z'.")) from None
        if prev_z is not None and z_val < prev_z - 1e-6:
            warnings.append(
                f"Layer Z is not monotonically increasing in plan order "
                f"(saw {z_val:.3f} after {prev_z:.3f}); NC generation will still sort by Z."
            )
        prev_z = z_val

    return warnings


__all__ = ["PlanSchemaError", "validate_plan_schema"]
