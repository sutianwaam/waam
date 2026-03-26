"""Minimal pipelines for generating and refining WAAM NC programs."""

from __future__ import annotations

import json
import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence

from ..core.config_loader import resolve_standard_config_path
from ..core.paths import get_features_path, get_operations_path, get_output_dir
from .waam_knowledge import WAAM_EXPERT_KNOWLEDGE
_MODULE_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _MODULE_ROOT / "config" / "standard_waam.json"
_DEFAULT_OUTPUT = get_output_dir()
_DEFAULT_FEATURES = get_features_path(_DEFAULT_OUTPUT, prefer_existing=True, create=True)
_DEFAULT_OPERATIONS = get_operations_path(_DEFAULT_OUTPUT, prefer_existing=True, create=True)
_DEFAULT_PLANNER_PLAN = _DEFAULT_OUTPUT / "waam_planner.json"
_COMPACT_FEATURE_LIMIT = 12
_COMPACT_OPERATION_LIMIT = 12

_PLANNER_PROMPT_TEMPLATE = """SYSTEM: You orchestrate deterministic WAAM planning.
USER: You receive CAD/mesh summaries plus bead, gas, dwell, and constraint data. Follow this loop:
1) Select an infill style per feature (contour, zigzag, contour+zigzag, spiral, or clearly named custom) with rationale.
2) Order the features respecting fixtures, heat limits, and dependency constraints.
3) Build a parameter grid covering bead width/overlap, gas pre/postflow, interpass dwell, travel and wire feeds (stay within limits).
4) Describe required toolchain calls (slicer/Grasshopper, simulation, NC post) with inputs/outputs.
5) Emit a JSON manifest listing layers/paths/parameters that is ready for NC posting.

Reply ONLY with JSON:
{{
  "planner_version": "llama3/v1",
  "infill_strategy": "...",
  "feature_sequence": [...],
  "parameter_grid": [...],
  "toolchain_calls": [...],
  "simulation_summary": {{...}},
  "layers": [...],
  "nc_post": {{"ready": false, "notes": "..."}},
  "warnings": ["..."]
}}

CONTEXT:
{json_spec}
"""

_PLAN_TO_NC_PROMPT_TEMPLATE = """SYSTEM: You are the WAAM NC assistant. Convert the supplied planner JSON into the safest,
highest-fidelity deposition program for the target controller.
USER: Inputs are:
- zero or one reference images (camera/thermal/operator snapshot),
- a deterministic planner JSON containing feature ordering, parameters, layers, and toolchain notes.

Tasks:
1) Respect every parameter limit and warning in the planner JSON.
2) Optimise torch/gas sequencing, ramps, and interpass dwell to reflect the plan.
3) Emit a controller-ready NC program (Fanuc-compatible unless a better dialect is explicitly justified).
4) Return ONLY JSON:
{{
  "summary": "... high-level plan ...",
  "warnings": ["... issues or TODOs ..."],
  "gcode": "G21...\\n..."
}}
{image_block}
PLANNER_JSON:
{json_plan}
"""

_JSON_TO_NC_PROMPT_TEMPLATE = """SYSTEM: You are an experienced WAAM NC programmer.
Use the deterministic WAAM JSON spec and the mesh context summary (derived from mesh_for_llm.txt or OBJ metadata) to emit the safest,
highest fidelity first-pass NC program for the documented machine and torch.

Inputs:
- `json_path` identifies the JSON source (typically waam_operations.json).
- `waam_json` contains the full deterministic deposition plan.
- `mesh_summary` provides structured mesh metadata plus a compact vertex/face listing when available.

Requirements:
1) Respect machine limits and WCS from the JSON metadata.
2) Sequence layers/features exactly as described.
3) Include torch, gas, dwell, and interpass handling from the JSON.
4) Emit comments that map segments back to feature/layer identifiers when possible.
5) Reply ONLY with JSON:
{{
  "summary": "... high-level overview ...",
  "warnings": ["... any cautions or TODOs ..."],
  "gcode": "G21...\\n..."
}}

JSON_SPEC:
{json_spec}

MESH_SUMMARY:
{mesh_summary}

EXPERTISE:
{waam_knowledge}
"""

_REFINE_NC_PROMPT_TEMPLATE = """SYSTEM: You revise WAAM NC programs.
You receive:
- The deterministic WAAM JSON spec and mesh summary (ground truth for geometry + process limits).
- The current NC program.
- An optional history of prior change requests/responses.
- The latest change request (user intent).

Tasks:
1) Explain the applied modifications in `summary`.
2) Flag any safety or feasibility concerns in `warnings`.
3) Emit the full updated NC program in `gcode`.
4) Preserve untouched sections where possible; focus on the requested edits.
5) Reply ONLY with JSON:
{{
  "summary": "... what changed ...",
  "warnings": ["... issues ..."],
  "gcode": "G21...\\n..."
}}
6) CRITICAL: You must return the COMPLETE NC program, even if it is large. Do NOT use placeholders like "... rest of code ...". Do NOT return only the diffs. The user needs the full file to run on the machine.

JSON_SPEC:
{json_spec}

MESH_SUMMARY:
{mesh_summary}

{geometry_context}

CURRENT_NC_PROGRAM:
```nc
{current_program}
```

CHANGE_HISTORY:
{change_history}

NEW_REQUEST:
{change_request}

EXPERTISE:
{waam_knowledge}
"""

_EDIT_NC_PROMPT_TEMPLATE = """SYSTEM: You revise WAAM NC programs.
You receive:
- The current NC program.
- An optional history of prior change requests/responses.
- The latest change request (user intent).

Tasks:
1) Explain the applied modifications in `summary`.
2) Flag any safety or feasibility concerns in `warnings`.
3) Emit the full updated NC program in `gcode`.
4) Preserve untouched sections where possible; focus on the requested edits.
5) Reply ONLY with JSON:
{{
  "summary": "... what changed ...",
  "warnings": ["... issues ..."],
  "gcode": "G21...\\n..."
}}
6) CRITICAL: You must return the COMPLETE NC program. Do NOT use placeholders. Do NOT return only the diffs.

PROGRAM_PATH:
{program_path}

CURRENT_NC_PROGRAM:
```nc
{current_program}
```

CHANGE_HISTORY:
{change_history}

NEW_REQUEST:
{change_request}
"""
_QUALITY_IMPROVEMENT_PROMPT_TEMPLATE = """SYSTEM: You are an expert WAAM NC programmer.
Your goal: Improve the G-code quality by analyzing the planned path against simulation results and diagnostic error data.

You receive a "Data Package" with 5 components.

<DATA_PACKAGE>

<BASELINE_NC>
{baseline_nc}
</BASELINE_NC>

<OPERATIONS_PLAN>
{operations_json}
</OPERATIONS_PLAN>

<LLM_GEOMETRY>
{llm_geometry}
</LLM_GEOMETRY>

<COVERAGE_ANALYSIS>
{coverage_json}
</COVERAGE_ANALYSIS>

<MISSING_POINTS>
{missing_points_json}
</MISSING_POINTS>

<USER_INSTRUCTIONS>
{extra_instructions}
</USER_INSTRUCTIONS>

</DATA_PACKAGE>

Tasks:
1) Analyze the `<MISSING_POINTS>` and `<USER_INSTRUCTIONS>`.
2) Correlate errors with `<BASELINE_NC>`.
3) Generate an IMPROVED NC program that:
    - Adds targeted "repair" passes or "kick" moves.
    - Strictly follows `<OPERATIONS_PLAN>`.
    - Address specific requests in `<USER_INSTRUCTIONS>`.

<OUTPUT_FORMAT>
Return ONLY JSON. No Markdown fencing, no Python code, just raw JSON.
{{
  "summary": "Brief analysis of the errors and the fix strategy.",
  "gcode": "G21\\nG90\\n..."
}}
</OUTPUT_FORMAT>

<CRITICAL_INSTRUCTIONS>
1. **ABSOLUTELY NO PYTHON CODE.** You are a G-code generator.
2. **CALCULATE INTERNALLY.** Do not write a script.
3. **PATH PRECISION:**
   - **SHOULD PASS:** Route torch through `<MISSING_POINTS>`.
   - **SHOULD NOT PASS:** Stay inside `<LLM_GEOMETRY>` hull.
4. **NO TRUNCATION:** Do NOT use `...` or ellipses in the G-code. The repair path must be fully written out.
5. **CHUNKING:** If the file is too large, output only the *entire* repair segment, but not the whole baseline.
6. **EXAMPLE INTERACTION:**
   - Input: Missing Point at (100.0, 50.0).
   - Output (G-code): `G0 X100.0 Y50.0 (Move to fix)...`
</CRITICAL_INSTRUCTIONS>
"""

class SpecLoadError(RuntimeError):
    """Raised when a required JSON artefact cannot be read."""


@dataclass(frozen=True)
class VlmPayload:
    """Container for a VLM-ready prompt plus the underlying spec."""

    prompt: str
    spec: Mapping[str, Any]
    images: Sequence[str]


def _ensure_path(path_like: Optional[Path | str], fallback: Path) -> Path:
    if path_like is None:
        return fallback
    return Path(path_like).expanduser().resolve()


def _read_json(path: Path, *, required: bool) -> MutableMapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if required:
            raise SpecLoadError(f"Required JSON artefact is missing: {path}") from None
        return {}
    except OSError as exc:  # pragma: no cover - filesystem specific
        if required:
            raise SpecLoadError(f"Unable to read JSON artefact ({path}): {exc}") from exc
        return {}

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecLoadError(f"JSON decode failed for {path}: {exc}") from exc
    if not isinstance(data, MutableMapping):
        raise SpecLoadError(f"JSON file {path} must contain an object at the top level.")
    return data


def _summarize(features: Mapping[str, Any], operations: Mapping[str, Any]) -> Dict[str, Any]:
    feature_items: List[Mapping[str, Any]] = list(features.get("features") or [])
    counts = Counter(item.get("type", "unknown") for item in feature_items)
    bbox = (features.get("metadata") or {}).get("bbox")

    seq = list(operations.get("sequence") or [])
    tool_names = [tool.get("name") for tool in operations.get("tools") or [] if tool.get("name")]

    return {
        "total_features": len(feature_items),
        "feature_types": counts,
        "bbox": bbox,
        "total_operations": len(seq),
        "operations_sample": [op.get("op") for op in seq[:5]],
        "tools": tool_names,
    }


def collect_waam_spec(
    *,
    config_path: Optional[Path | str] = None,
    features_path: Optional[Path | str] = None,
    operations_path: Optional[Path | str] = None,
    strict: bool = True,
) -> Dict[str, Any]:
    """Load the WAAM config, extracted features, and operation plan."""

    config_file = _ensure_path(config_path, _DEFAULT_CONFIG)
    features_file = _ensure_path(features_path, _DEFAULT_FEATURES)
    operations_file = _ensure_path(operations_path, _DEFAULT_OPERATIONS)

    config = _read_json(config_file, required=strict)
    features = _read_json(features_file, required=strict)
    operations = _read_json(operations_file, required=strict)

    spec = {
        "config": config,
        "features": features,
        "operations": operations,
        "summary": _summarize(features, operations),
        "sources": {
            "config_path": str(config_file),
            "features_path": str(features_file),
            "operations_path": str(operations_file),
        },
    }
    return spec


def _normalize_images(images: Optional[Iterable[str]]) -> List[str]:
    if not images:
        return []
    normalized: List[str] = []
    for item in images:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _image_block(images: Sequence[str]) -> str:
    if images:
        return "\n".join(f"IMAGE: {image}" for image in images)
    return "IMAGE: <none provided>"


def _planner_focus_spec(spec: Mapping[str, Any]) -> Dict[str, Any]:
    config = spec.get("config") or {}
    process = config.get("process") or {}
    parameters = config.get("parameters") or {}
    machine = config.get("machine") or {}

    return {
        "summary": spec.get("summary"),
        "features": spec.get("features"),
        "operations": spec.get("operations"),
        "process": {
            "deposition": process.get("deposition"),
            "finishing": process.get("finishing"),
            "alternate_modes": process.get("alternate_modes"),
            "interpass": process.get("interpass"),
            "path": process.get("path"),
        },
        "parameters": parameters,
        "machine": {
            "name": machine.get("name"),
            "travel": machine.get("travel"),
            "max_feed_xy": machine.get("max_feed_xy"),
            "max_feed_z": machine.get("max_feed_z"),
            "max_wire_feed_mm_min": machine.get("max_wire_feed_mm_min"),
            "cooling": machine.get("cooling"),
            "torch": machine.get("torch"),
            "positioner": machine.get("positioner"),
            "sensors": machine.get("sensors"),
        },
        "constraints": {
            "material": spec.get("config", {}).get("material") if isinstance(spec.get("config"), Mapping) else None,
            "quality": spec.get("config", {}).get("quality"),
            "safety": spec.get("config", {}).get("safety"),
        },
        "sources": spec.get("sources"),
    }


def format_planner_prompt(spec: Mapping[str, Any]) -> str:
    spec_json = json.dumps(spec, indent=2)
    return _PLANNER_PROMPT_TEMPLATE.format(json_spec=spec_json)


def format_plan_to_nc_prompt(plan: Mapping[str, Any], *, images: Optional[Iterable[str]] = None) -> str:
    image_list = _normalize_images(images)
    image_block = _image_block(image_list)
    plan_json = json.dumps(plan, indent=2)
    return _PLAN_TO_NC_PROMPT_TEMPLATE.format(image_block=image_block, json_plan=plan_json)


def format_json_to_nc_prompt(spec: Mapping[str, Any]) -> str:
    json_block = json.dumps(
        {
            "json_path": spec.get("json_path"),
            "waam_json": spec.get("waam_json"),
            "config_path": spec.get("config_path"),
            "config": spec.get("config"),
        },
        indent=2,
    )
    mesh_block = json.dumps(spec.get("mesh_summary"), indent=2)
    mesh_block = json.dumps(spec.get("mesh_summary"), indent=2)
    return _JSON_TO_NC_PROMPT_TEMPLATE.format(
        json_spec=json_block,
        mesh_summary=mesh_block,
        waam_knowledge=WAAM_EXPERT_KNOWLEDGE
    )


def _format_change_history(history: Sequence[Mapping[str, str]]) -> str:
    if not history:
        return "None."
    lines: List[str] = []
    for idx, item in enumerate(history[-8:], 1):
        role = item.get("role", "user")
        content = item.get("content", "").strip()
        lines.append(f"{idx}. {role}: {content}")
    return "\n".join(lines)


def format_refine_prompt(
    spec: Mapping[str, Any],
    *,
    current_program: str,
    change_request: str,
    history: Sequence[Mapping[str, str]],
    geometry_context: str = "",
) -> str:
    json_block = json.dumps(
        {
            "json_path": spec.get("json_path"),
            "waam_json": spec.get("waam_json"),
            "config_path": spec.get("config_path"),
            "config": spec.get("config"),
        },
        indent=2,
    )
    mesh_block = json.dumps(spec.get("mesh_summary"), indent=2)
    history_block = _format_change_history(history)
    return _REFINE_NC_PROMPT_TEMPLATE.format(
        json_spec=json_block,
        mesh_summary=mesh_block,
        geometry_context=geometry_context,
        current_program=current_program.strip(),
        change_history=history_block,
        change_request=change_request.strip() or "User did not provide additional detail.",
        waam_knowledge=WAAM_EXPERT_KNOWLEDGE,
    )


def format_nc_edit_prompt(
    *,
    current_program: str,
    change_request: str,
    history: Sequence[Mapping[str, str]],
    program_path: Optional[str] = None,
) -> str:
    history_block = _format_change_history(history)
    return _EDIT_NC_PROMPT_TEMPLATE.format(
        program_path=program_path or "<unspecified>",
        current_program=current_program.strip(),
        change_history=history_block,
        change_request=change_request.strip() or "User did not provide additional detail.",
    )


def _compact_spec(spec: Mapping[str, Any]) -> Dict[str, Any]:
    features = spec.get("features") or {}
    operations = spec.get("operations") or {}
    config = spec.get("config") or {}
    summary = spec.get("summary") or {}

    def _slice_list(items: Any, limit: int):
        if isinstance(items, list):
            return items[:limit]
        return items

    return {
        "summary": summary,
        "sources": spec.get("sources"),
        "config_metadata": config.get("metadata"),
        "process_deposition": (config.get("process") or {}).get("deposition"),
        "feature_metadata": features.get("metadata"),
        "feature_samples": _slice_list(features.get("features"), _COMPACT_FEATURE_LIMIT),
        "operation_metadata": operations.get("metadata"),
        "operation_samples": _slice_list(operations.get("sequence"), _COMPACT_OPERATION_LIMIT),
        "tools": _slice_list(operations.get("tools"), 5),
    }


def build_planner_payload(
    *,
    images: Optional[Iterable[str]] = None,
    config_path: Optional[Path | str] = None,
    features_path: Optional[Path | str] = None,
    operations_path: Optional[Path | str] = None,
    strict: bool = True,
    compact: bool = False,
) -> VlmPayload:
    """Load the WAAM spec and format the planner prompt for the planner model."""

    spec = collect_waam_spec(
        config_path=config_path,
        features_path=features_path,
        operations_path=operations_path,
        strict=strict,
    )
    spec_for_prompt = _compact_spec(spec) if compact else spec
    planner_spec = _planner_focus_spec(spec_for_prompt)
    prompt = format_planner_prompt(planner_spec)
    return VlmPayload(prompt=prompt, spec=planner_spec, images=_normalize_images(images))


def load_planner_plan(path: Optional[Path | str], *, strict: bool = True) -> Mapping[str, Any]:
    """Load the planner JSON emitted by the reasoning stage."""

    planner_file = _ensure_path(path, _DEFAULT_PLANNER_PLAN)
    plan = _read_json(planner_file, required=strict)
    plan.setdefault("sources", {})
    plan["sources"]["planner_path"] = str(planner_file)
    return plan


def build_plan_to_nc_payload(
    *,
    planner_path: Optional[Path | str] = None,
    images: Optional[Iterable[str]] = None,
    strict: bool = True,
) -> VlmPayload:
    """Format the NC-generation prompt using the planner JSON (plus optional images)."""

    plan = load_planner_plan(planner_path, strict=strict)
    prompt = format_plan_to_nc_prompt(plan, images=images)
    return VlmPayload(prompt=prompt, spec=plan, images=_normalize_images(images))


def _summarize_obj_file(path: Path, *, max_snippet_lines: int = 120) -> Dict[str, Any]:
    if not path.is_file():
        raise SpecLoadError(f"Mesh OBJ not found: {path}")

    size_bytes = path.stat().st_size
    hasher = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            if not chunk:
                break
            hasher.update(chunk)

    vertex_count = 0
    face_count = 0
    bbox_min = [None, None, None]
    bbox_max = [None, None, None]
    snippet: List[str] = []

    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for idx, line in enumerate(handle):
            stripped = line.strip()
            if idx < max_snippet_lines:
                snippet.append(stripped)
            if not stripped:
                continue
            if stripped.startswith("v "):
                vertex_count += 1
                parts = stripped.split()
                if len(parts) >= 4:
                    try:
                        coords = [float(parts[1]), float(parts[2]), float(parts[3])]
                    except ValueError:
                        continue
                    for axis in range(3):
                        value = coords[axis]
                        if bbox_min[axis] is None or value < bbox_min[axis]:
                            bbox_min[axis] = value
                        if bbox_max[axis] is None or value > bbox_max[axis]:
                            bbox_max[axis] = value
            elif stripped.startswith("f "):
                face_count += 1

    def _clean(values: List[Optional[float]]) -> Optional[List[float]]:
        if any(v is None for v in values):
            return None
        return [float(v) for v in values]  # type: ignore[arg-type]

    return {
        "path": str(path),
        "size_bytes": size_bytes,
        "sha1": hasher.hexdigest(),
        "vertices": vertex_count,
        "faces": face_count,
        "bbox_min": _clean(bbox_min),
        "bbox_max": _clean(bbox_max),
        "sample_lines": snippet,
        "format": "obj",
    }


def _summarize_llm_mesh_text(path: Path, *, max_chars: int = 400000) -> Dict[str, Any]:
    if not path.is_file():
        raise SpecLoadError(f"Mesh context file not found: {path}")

    contents = path.read_text(encoding="utf-8", errors="ignore")
    truncated = False
    if len(contents) > max_chars:
        contents = contents[:max_chars]
        truncated = True

    lines = contents.splitlines()
    vertex_count = None
    face_count = None
    bbox_min = None
    bbox_max = None
    vertices_preview: list[str] = []
    faces_preview: list[str] = []
    in_vertices = False
    in_faces = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        tag = parts[0].upper()
        if tag in {"TOTAL_VERTICES", "VERTICES"} and len(parts) > 1:
            try:
                vertex_count = int(parts[1])
            except ValueError:
                vertex_count = None
        elif tag in {"TOTAL_FACES", "FACES"} and len(parts) > 1:
            try:
                face_count = int(parts[1])
            except ValueError:
                face_count = None
        elif tag == "BBOX_MIN" and len(parts) >= 4:
            try:
                bbox_min = [float(parts[1]), float(parts[2]), float(parts[3])]
            except ValueError:
                bbox_min = None
        elif tag == "BBOX_MAX" and len(parts) >= 4:
            try:
                bbox_max = [float(parts[1]), float(parts[2]), float(parts[3])]
            except ValueError:
                bbox_max = None
        elif tag == "VERTICES_BEGIN":
            in_vertices = True
            in_faces = False
            continue
        elif tag == "VERTICES_END":
            in_vertices = False
            continue
        elif tag == "FACES_BEGIN":
            in_faces = True
            in_vertices = False
            continue
        elif tag == "FACES_END":
            in_faces = False
            continue

        if in_vertices and len(vertices_preview) < 12:
            vertices_preview.append(stripped)
        if in_faces and len(faces_preview) < 12:
            faces_preview.append(stripped)

    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "format": "mesh_for_llm_txt",
        "vertices": vertex_count,
        "faces": face_count,
        "bbox_min": bbox_min,
        "bbox_max": bbox_max,
        "truncated": truncated,
        "mesh_text": contents,
        "samples": {
            "vertices": vertices_preview,
            "faces": faces_preview,
        },
    }


def _summarize_mesh_context(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix == ".txt" or name.endswith("mesh_for_llm") or name.endswith("mesh_for_llm.txt"):
        return _summarize_llm_mesh_text(path)
    return _summarize_obj_file(path)


def _load_config_data(config_path: Optional[Path | str]) -> tuple[Optional[Path], Mapping[str, Any]]:
    resolved: Optional[Path]
    if config_path:
        resolved = Path(config_path).expanduser().resolve()
    else:
        try:
            resolved = resolve_standard_config_path()
        except FileNotFoundError:
            resolved = None
    if resolved is None:
        return None, {}
    if not resolved.is_file():
        if config_path:
            raise SpecLoadError(f"WAAM config not found: {resolved}")
        return None, {}
    data = _read_json(resolved, required=False)
    return resolved, data


def _load_json_mesh_context(
    *,
    json_path: Optional[Path | str],
    mesh_path: Optional[Path | str],
    config_path: Optional[Path | str],
    strict: bool,
) -> Dict[str, Any]:
    json_file = _ensure_path(json_path, _DEFAULT_OPERATIONS)
    plan = _read_json(json_file, required=strict)
    mesh_file = Path(mesh_path).expanduser().resolve() if mesh_path else None
    if mesh_file is None or not mesh_file.is_file():
        raise SpecLoadError("Select a valid mesh context file for NC generation.")
    mesh_summary = _summarize_mesh_context(mesh_file)
    cfg_path, cfg_data = _load_config_data(config_path)
    return {
        "json_path": str(json_file),
        "waam_json": plan,
        "mesh_summary": mesh_summary,
        "config_path": str(cfg_path) if cfg_path else None,
        "config": cfg_data,
    }


def build_json_to_nc_payload(
    *,
    json_path: Optional[Path | str] = None,
    mesh_path: Optional[Path | str] = None,
    config_path: Optional[Path | str] = None,
    strict: bool = True,
) -> VlmPayload:
    """Format the prompt for generating an NC program directly from WAAM JSON + mesh context."""

    context = _load_json_mesh_context(
        json_path=json_path,
        mesh_path=mesh_path,
        config_path=config_path,
        strict=strict,
    )
    prompt = format_json_to_nc_prompt(context)
    return VlmPayload(prompt=prompt, spec=context, images=[])


def build_refine_nc_payload(
    *,
    json_path: Optional[Path | str] = None,
    mesh_path: Optional[Path | str] = None,
    config_path: Optional[Path | str] = None,
    base_program: str,
    change_request: str,
    history: Optional[Sequence[Mapping[str, str]]] = None,
    strict: bool = True,
    extra_instructions: Optional[str] = None,
) -> VlmPayload:
    """Format the prompt for refining an existing NC program."""

    if not base_program.strip():
        raise SpecLoadError("Provide an existing NC program before requesting a refinement.")
    if not change_request.strip():
        raise SpecLoadError("Describe the requested change before refining the NC program.")
    context = _load_json_mesh_context(
        json_path=json_path,
        mesh_path=mesh_path,
        config_path=config_path,
        strict=strict,
    )
    prompt = format_refine_prompt(
        context,
        current_program=base_program,
        change_request=change_request,
        history=history or [],
        geometry_context=extra_instructions or "",
    )
    return VlmPayload(prompt=prompt, spec=context, images=[])

def build_nc_edit_payload(
    *,
    base_program: str,
    change_request: str,
    history: Optional[Sequence[Mapping[str, str]]] = None,
    program_path: Optional[str] = None,
) -> VlmPayload:
    """Format the prompt for editing an existing NC program without JSON/mesh context."""

    if not base_program.strip():
        raise SpecLoadError("Provide an existing NC program before requesting a refinement.")
    if not change_request.strip():
        raise SpecLoadError("Describe the requested change before refining the NC program.")
    prompt = format_nc_edit_prompt(
        current_program=base_program,
        change_request=change_request,
        history=history or [],
        program_path=program_path,
    )
    spec = {
        "program_path": program_path,
    }
    return VlmPayload(prompt=prompt, spec=spec, images=[])


__all__ = [
    "SpecLoadError",
    "VlmPayload",
    "collect_waam_spec",
    "build_planner_payload",
    "build_plan_to_nc_payload",
    "load_planner_plan",
    "format_planner_prompt",
    "format_plan_to_nc_prompt",
    "format_json_to_nc_prompt",
    "format_refine_prompt",
    "build_json_to_nc_payload",
    "build_refine_nc_payload",
    "format_nc_edit_prompt",
    "build_nc_edit_payload",
    "_DEFAULT_PLANNER_PLAN",
]
