"""Lightweight WAAM assistant helpers."""

from __future__ import annotations

from .ollama_client import (
    PlanToNcResult,
    PlannerStageResult,
    run_json_to_nc_generation,
    run_nc_edit,
    run_nc_refinement,
    run_plan_to_nc_generation,
    run_planner_stage,
)
from .api import (
    DEFAULT_PLANNER_PLAN,
    SpecLoadError,
    VlmPayload,
    PlannerPayloadRequest,
    PlanToNcRequest,
    JsonToNcRequest,
    RefineNcRequest,
    NcEditRequest,
    build_json_to_nc_payload,
    build_plan_to_nc_payload,
    build_planner_payload,
    build_refine_nc_payload,
    build_nc_edit_payload,
    collect_waam_spec,
    format_json_to_nc_prompt,
    format_plan_to_nc_prompt,
    format_planner_prompt,
    format_refine_prompt,
    format_nc_edit_prompt,
    load_planner_plan,
)
from .step_to_waam_dsl import DSL_VERSION, build_waam_dsl, write_waam_dsl

try:  # pragma: no cover - FreeCAD may be unavailable in headless runs
    from .interface import chat_with_llm, gather_default_context
except Exception as exc:  # pragma: no cover
    def _interface_stub(*_args, **_kwargs):
        raise RuntimeError(
            "FreeCAD GUI context not available; chat_with_llm() is disabled."
        ) from exc

    chat_with_llm = gather_default_context = _interface_stub

__all__ = [
    "chat_with_llm",
    "gather_default_context",
    "SpecLoadError",
    "VlmPayload",
    "PlannerPayloadRequest",
    "PlanToNcRequest",
    "JsonToNcRequest",
    "RefineNcRequest",
    "NcEditRequest",
    "PlannerStageResult",
    "PlanToNcResult",
    "DEFAULT_PLANNER_PLAN",
    "build_planner_payload",
    "build_plan_to_nc_payload",
    "build_json_to_nc_payload",
    "build_refine_nc_payload",
    "build_nc_edit_payload",
    "collect_waam_spec",
    "format_planner_prompt",
    "format_plan_to_nc_prompt",
    "format_json_to_nc_prompt",
    "format_refine_prompt",
    "format_nc_edit_prompt",
    "load_planner_plan",
    "run_planner_stage",
    "run_plan_to_nc_generation",
    "run_json_to_nc_generation",
    "run_nc_edit",
    "run_nc_refinement",
    "build_waam_dsl",
    "write_waam_dsl",
    "DSL_VERSION",
]
