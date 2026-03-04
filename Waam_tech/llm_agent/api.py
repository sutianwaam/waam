from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from . import llm_pipeline


SpecLoadError = llm_pipeline.SpecLoadError
VlmPayload = llm_pipeline.VlmPayload
DEFAULT_PLANNER_PLAN = llm_pipeline._DEFAULT_PLANNER_PLAN


@dataclass(frozen=True)
class PlannerPayloadRequest:
    images: Optional[Iterable[str]] = None
    config_path: Optional[Path | str] = None
    features_path: Optional[Path | str] = None
    operations_path: Optional[Path | str] = None
    strict: bool = True
    compact: bool = False


@dataclass(frozen=True)
class PlanToNcRequest:
    planner_path: Optional[Path | str] = None
    images: Optional[Iterable[str]] = None
    strict: bool = True


@dataclass(frozen=True)
class JsonToNcRequest:
    json_path: Optional[Path | str] = None
    mesh_path: Optional[Path | str] = None
    config_path: Optional[Path | str] = None
    strict: bool = True


@dataclass(frozen=True)
class RefineNcRequest:
    json_path: Optional[Path | str] = None
    mesh_path: Optional[Path | str] = None
    config_path: Optional[Path | str] = None
    base_program: str = ""
    change_request: str = ""
    history: Optional[Sequence[Mapping[str, str]]] = None
    strict: bool = True


@dataclass(frozen=True)
class NcEditRequest:
    base_program: str = ""
    change_request: str = ""
    history: Optional[Sequence[Mapping[str, str]]] = None
    program_path: Optional[Path | str] = None


def build_planner_payload(req: PlannerPayloadRequest | None = None, **kwargs) -> VlmPayload:
    request = req or PlannerPayloadRequest(**kwargs)
    return llm_pipeline.build_planner_payload(
        images=request.images,
        config_path=request.config_path,
        features_path=request.features_path,
        operations_path=request.operations_path,
        strict=request.strict,
        compact=request.compact,
    )


def build_plan_to_nc_payload(req: PlanToNcRequest | None = None, **kwargs) -> VlmPayload:
    request = req or PlanToNcRequest(**kwargs)
    return llm_pipeline.build_plan_to_nc_payload(
        planner_path=request.planner_path,
        images=request.images,
        strict=request.strict,
    )


def build_json_to_nc_payload(req: JsonToNcRequest | None = None, **kwargs) -> VlmPayload:
    request = req or JsonToNcRequest(**kwargs)
    return llm_pipeline.build_json_to_nc_payload(
        json_path=request.json_path,
        mesh_path=request.mesh_path,
        config_path=request.config_path,
        strict=request.strict,
    )


def build_refine_nc_payload(req: RefineNcRequest | None = None, **kwargs) -> VlmPayload:
    request = req or RefineNcRequest(**kwargs)
    return llm_pipeline.build_refine_nc_payload(
        json_path=request.json_path,
        mesh_path=request.mesh_path,
        config_path=request.config_path,
        base_program=request.base_program,
        change_request=request.change_request,
        history=request.history,
        strict=request.strict,
    )


def build_nc_edit_payload(req: NcEditRequest | None = None, **kwargs) -> VlmPayload:
    request = req or NcEditRequest(**kwargs)
    return llm_pipeline.build_nc_edit_payload(
        base_program=request.base_program,
        change_request=request.change_request,
        history=request.history,
        program_path=str(request.program_path) if request.program_path else None,
    )


def load_planner_plan(path: Optional[Path | str], *, strict: bool = True):
    return llm_pipeline.load_planner_plan(path, strict=strict)



def collect_waam_spec(
    *,
    config_path: Optional[Path | str] = None,
    features_path: Optional[Path | str] = None,
    operations_path: Optional[Path | str] = None,
    strict: bool = True,
):
    return llm_pipeline.collect_waam_spec(
        config_path=config_path,
        features_path=features_path,
        operations_path=operations_path,
        strict=strict,
    )


def build_quality_improvement_payload(**kwargs) -> VlmPayload:
    return llm_pipeline.build_quality_improvement_payload(**kwargs)


def format_planner_prompt(spec: Mapping[str, object]) -> str:
    return llm_pipeline.format_planner_prompt(spec)


def format_plan_to_nc_prompt(plan: Mapping[str, object], images: Optional[Iterable[str]] = None) -> str:
    return llm_pipeline.format_plan_to_nc_prompt(plan, images=images)


def format_json_to_nc_prompt(context: Mapping[str, object]) -> str:
    return llm_pipeline.format_json_to_nc_prompt(context)


def format_refine_prompt(context: Mapping[str, object]) -> str:
    return llm_pipeline.format_refine_prompt(context)


def format_nc_edit_prompt(
    *,
    current_program: str,
    change_request: str,
    history: Sequence[Mapping[str, str]],
    program_path: Optional[str] = None,
) -> str:
    return llm_pipeline.format_nc_edit_prompt(
        current_program=current_program,
        change_request=change_request,
        history=history,
        program_path=program_path,
    )


def summarize_spec(features: Mapping[str, object], operations: Mapping[str, object]) -> Mapping[str, object]:
    return llm_pipeline._summarize(features, operations)


def summarize_mesh_context(path: Path) -> Mapping[str, object]:
    return llm_pipeline._summarize_mesh_context(path)
