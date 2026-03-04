"""Invoke local Ollama models for the simplified WAAM planner➜NC pipeline."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, List

from .api import (
    DEFAULT_PLANNER_PLAN,
    VlmPayload,
    build_nc_edit_payload,
    build_json_to_nc_payload,
    build_plan_to_nc_payload,
    build_planner_payload,
    build_planner_payload,
    build_refine_nc_payload,
    build_quality_improvement_payload,
)
from ..core.paths import get_output_dir


# models:
# kimi-k2-thinking:cloud
# gpt-oss:latest
# qwen3-vl:8b
# llama3

_DEFAULT_PLANNER_MODEL = os.environ.get("WAAM_PLANNER_MODEL", "gpt-oss:latest")
_DEFAULT_NC_MODEL = os.environ.get("WAAM_NC_MODEL", "gpt-oss:latest")


class OllamaNotAvailableError(RuntimeError):
    """Raised when the Ollama CLI is missing from PATH."""


class OllamaInvocationError(RuntimeError):
    """Raised when the Ollama CLI returns a non-zero exit code."""


@dataclass(frozen=True)
class PlannerStageResult:
    """Reasoning-stage output of the planner (Llama 3)."""

    payload: VlmPayload
    raw_output: str
    parsed_json: Optional[Mapping[str, Any]]
    saved_path: Optional[Path]


@dataclass(frozen=True)
class PlanToNcResult:
    """NC-generation output (Qwen)."""

    payload: VlmPayload
    raw_output: str
    parsed_json: Optional[Mapping[str, Any]]
    gcode: Optional[str]


def _ensure_ollama() -> None:
    if shutil.which("ollama") is None:
        raise OllamaNotAvailableError(
            "Ollama CLI not found. Install it from https://ollama.ai/ and ensure it is on PATH."
        )


def check_ollama_available() -> tuple[bool, str]:
    """
    Check if Ollama is available and ready to use.
    
    Returns:
        (is_available, message): bool indicating availability and a user-friendly message
    """
    if shutil.which("ollama") is None:
        return False, (
            "Ollama is not installed or not in PATH.\n\n"
            "To use local LLM features:\n"
            "1. Install Ollama from https://ollama.ai/\n"
            "2. Ensure 'ollama' command is available in your terminal\n"
            "3. Run 'ollama pull gpt-oss' to download a model"
        )
    
    # Check if Ollama service is running by listing models
    try:
        subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=15,  # Increased from 5s to handle slower responses
            check=True,
        )
        return True, "Ollama is ready"
    except subprocess.TimeoutExpired:
        return False, (
            "Ollama command timed out.\n\n"
            "The Ollama service may not be running. Try:\n"
            "1. Start Ollama service (it should auto-start after installation)\n"
            "2. Run 'ollama serve' in a terminal"
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        return False, f"Ollama error: {stderr or 'Unable to connect to Ollama service'}"
    except Exception as exc:
        return False, f"Unexpected error checking Ollama: {exc}"


def list_available_models() -> list[str]:
    """
    List all locally available Ollama models.
    
    Returns:
        List of model names (e.g., ['gpt-oss:latest', 'llama3:latest'])
    """
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=20,  # Increased from 10s to handle slower responses
            check=True,
        )
        lines = result.stdout.strip().splitlines()
        models: list[str] = []
        
        # Parse output (skip header line)
        for line in lines[1:]:
            parts = line.split()
            if parts:
                # First column is the model name
                model_name = parts[0].strip()
                if model_name:
                    models.append(model_name)
        
        return models
    except Exception:
        # Return empty list if we can't get models
        return []


def check_model_available(model_name: str) -> tuple[bool, str]:
    """
    Check if a specific model is pulled and available.
    
    Returns:
        (is_available, message): bool and a user-friendly message
    """
    available_models = list_available_models()
    
    if not available_models:
        return False, (
            f"Cannot verify if model '{model_name}' is available.\n\n"
            "Unable to list Ollama models. Ensure Ollama is running."
        )
    
    # Check for exact match or partial match (e.g., "llama3" matches "llama3:latest")
    for avail in available_models:
        if avail == model_name or avail.startswith(f"{model_name}:"):
            return True, f"Model '{model_name}' is ready"
    
    return False, (
        f"Model '{model_name}' is not installed.\n\n"
        f"Available models: {', '.join(available_models[:5])}\n\n"
        f"To install this model, run:\n"
        f"  ollama pull {model_name}"
    )


def _normalize_image(image: Optional[str]) -> Optional[str]:
    if not image:
        return None
    path = Path(image).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {path}")
    return str(path)


def _encode_image(path: str) -> str:
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("ascii")


def _run_ollama(cmd: Sequence[str], *, input_text: Optional[str]) -> str:
    try:
        completed = subprocess.run(
            list(cmd),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - depends on Ollama state
        stderr = (exc.stderr or "").strip()
        raise OllamaInvocationError(stderr or str(exc)) from exc
    return (completed.stdout or "").strip()


def _invoke_json_mode(model: str, prompt: str, image_paths: Sequence[str]) -> str:
    cmd = ["ollama", "run", model, "--json"]
    payload: dict[str, Any] = {"prompt": prompt}
    if image_paths:
        payload["images"] = [_encode_image(path) for path in image_paths]
    stdout = _run_ollama(cmd, input_text=json.dumps(payload))
    if not stdout:
        return ""
    chunks: list[str] = []
    fallback_lines = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        fallback_lines.append(stripped)
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        chunk = obj.get("response")
        if chunk:
            chunks.append(chunk)
    return "".join(chunks).strip() or "\n".join(fallback_lines)


def _invoke_legacy_mode(model: str, prompt: str, image_paths: Sequence[str]) -> str:
    cmd = ["ollama", "run", model]
    for path in image_paths:
        cmd.extend(["--img", path])
    try:
        return _run_ollama(cmd, input_text=prompt)
    except OllamaInvocationError as exc:
        message = str(exc)
        if image_paths and "--img" in message:
            note_lines = ["Image references (attach manually if needed):", *image_paths]
            fallback_prompt = f"{prompt}\n\nNOTE:\n" + "\n".join(note_lines)
            return _run_ollama(["ollama", "run", model], input_text=fallback_prompt)
        raise


def _invoke_ollama(model: str, prompt: str, image_paths: Sequence[str]) -> str:
    _ensure_ollama()
    try:
        return _invoke_json_mode(model, prompt, image_paths)
    except OllamaInvocationError as exc:
        message = str(exc).lower()
        if "unknown flag: --json" in message or "flag provided but not defined: -json" in message:
            return _invoke_legacy_mode(model, prompt, image_paths)
        raise


def _parse_json_block(text: str) -> Optional[Mapping[str, Any]]:
    text = text.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, Mapping):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = text[start : end + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, Mapping):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


_CODE_BLOCK_RE = re.compile(r"```(?P<lang>[a-zA-Z0-9_-]*)\s*(?P<body>.*?)```", re.DOTALL)


def _extract_code_block(text: str) -> Optional[str]:
    if not text:
        return None
    for match in _CODE_BLOCK_RE.finditer(text):
        lang = match.group("lang").lower()
        body = (match.group("body") or "").strip()
        if not body:
            continue
        if lang in ("gcode", "nc", ""):
            return body
    return None


def _maybe_append_instruction(prompt: str, extra: Optional[str]) -> str:
    if not extra:
        return prompt
    extra_text = extra.strip()
    if not extra_text:
        return prompt
    return f"{prompt.rstrip()}\n\nUSER_INSTRUCTION:\n{extra_text}\n"


def _load_history(path: Optional[str]) -> List[Mapping[str, str]]:
    if not path:
        return []
    history_path = Path(path).expanduser().resolve()
    if not history_path.is_file():
        return []
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    cleaned: List[Mapping[str, str]] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if not role or not content:
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned


def _write_history(path: str, history: Sequence[Mapping[str, str]]) -> None:
    history_path = Path(path).expanduser().resolve()
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(list(history), indent=2), encoding="utf-8")


def _append_history(history: List[Mapping[str, str]], role: str, content: str) -> None:
    text = (content or "").strip()
    if not text:
        return
    history.append({"role": role, "content": text})


def _summarize_assistant_response(parsed_json: Optional[Mapping[str, Any]], raw_output: str) -> str:
    if parsed_json:
        parts: List[str] = []
        summary = parsed_json.get("summary")
        if isinstance(summary, str) and summary.strip():
            parts.append(f"Summary: {summary.strip()}")
        warnings = parsed_json.get("warnings")
        if isinstance(warnings, list) and warnings:
            warn_texts = [str(item).strip() for item in warnings if str(item).strip()]
            if warn_texts:
                parts.append("Warnings: " + "; ".join(warn_texts))
        if parts:
            return "\n".join(parts)
    return (raw_output or "").strip()[:400]


def _infer_agent_prefix(input_path: Path) -> str:
    stem = input_path.stem
    match = re.match(r"(.+?)_(\d+)$", stem)
    if match:
        return match.group(1)
    if stem.endswith("_agent"):
        return stem
    return f"{stem}_agent"


def _next_agent_output(out_dir: Path, prefix: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(re.escape(prefix) + r"_(\d+)\.nc$")
    max_idx = 0
    for path in out_dir.glob(f"{prefix}_*.nc"):
        match = pattern.match(path.name)
        if match:
            try:
                idx = int(match.group(1))
            except ValueError:
                continue
            if idx > max_idx:
                max_idx = idx
    return out_dir / f"{prefix}_{max_idx + 1:03d}.nc"


def _write_nc_file(path: Path, gcode: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(gcode, encoding="utf-8")


def _write_json_file(path_like: str, data: Mapping[str, Any]) -> Path:
    path = Path(path_like).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def run_planner_stage(
    *,
    model: str = _DEFAULT_PLANNER_MODEL,
    config_path: Optional[str] = None,
    features_path: Optional[str] = None,
    operations_path: Optional[str] = None,
    strict: bool = True,
    extra_instructions: Optional[str] = None,
    compact: bool = False,
    save_path: Optional[str] = None,
) -> PlannerStageResult:
    """Execute the reasoning planner stage."""

    payload = build_planner_payload(
        images=None,
        config_path=config_path,
        features_path=features_path,
        operations_path=operations_path,
        strict=strict,
        compact=compact,
    )
    prompt = _maybe_append_instruction(payload.prompt, extra_instructions)
    raw_output = _invoke_ollama(model=model, prompt=prompt, image_paths=[])
    parsed_json = _parse_json_block(raw_output)
    saved_path = None
    target_path = save_path or str(DEFAULT_PLANNER_PLAN)
    if parsed_json:
        saved_path = _write_json_file(target_path, parsed_json)
    return PlannerStageResult(payload=payload, raw_output=raw_output, parsed_json=parsed_json, saved_path=saved_path)


def run_plan_to_nc_generation(
    *,
    model: str = _DEFAULT_NC_MODEL,
    planner_path: Optional[str] = None,
    image_path: Optional[str] = None,
    strict: bool = True,
    extra_instructions: Optional[str] = None,
) -> PlanToNcResult:
    """Generate an NC program from a planner JSON (plus optional imagery)."""

    resolved_image = _normalize_image(image_path)
    payload = build_plan_to_nc_payload(
        planner_path=planner_path,
        images=[Path(resolved_image).name] if resolved_image else [],
        strict=strict,
    )
    prompt = _maybe_append_instruction(payload.prompt, extra_instructions)
    raw_output = _invoke_ollama(
        model=model,
        prompt=prompt,
        image_paths=[resolved_image] if resolved_image else [],
    )
    parsed_json = _parse_json_block(raw_output)
    gcode = None
    if parsed_json:
        for key in ("gcode", "nc_program", "program"):
            value = parsed_json.get(key)
            if isinstance(value, str) and value.strip():
                gcode = value.strip()
                break
    if not gcode:
        gcode = _extract_code_block(raw_output)
    return PlanToNcResult(payload=payload, raw_output=raw_output, parsed_json=parsed_json, gcode=gcode)


def run_json_to_nc_generation(
    *,
    model: str = _DEFAULT_NC_MODEL,
    json_path: Optional[str] = None,
    mesh_path: Optional[str] = None,
    config_path: Optional[str] = None,
    strict: bool = True,
    extra_instructions: Optional[str] = None,
) -> PlanToNcResult:
    """Generate an NC program directly from WAAM JSON + mesh context."""

    payload = build_json_to_nc_payload(
        json_path=json_path,
        mesh_path=mesh_path,
        config_path=config_path,
        strict=strict,
    )
    prompt = _maybe_append_instruction(payload.prompt, extra_instructions)
    raw_output = _invoke_ollama(model=model, prompt=prompt, image_paths=[])
    parsed_json = _parse_json_block(raw_output)
    gcode = None
    if parsed_json:
        gcode = (parsed_json.get("gcode") or parsed_json.get("nc_program") or "").strip() or None
    if not gcode:
        gcode = _extract_code_block(raw_output)
    return PlanToNcResult(payload=payload, raw_output=raw_output, parsed_json=parsed_json, gcode=gcode)


def run_nc_edit(
    *,
    model: str = _DEFAULT_NC_MODEL,
    base_program: str,
    change_request: str,
    history: Optional[Sequence[Mapping[str, str]]] = None,
    program_path: Optional[str] = None,
    extra_instructions: Optional[str] = None,
) -> PlanToNcResult:
    """Edit an existing NC program without JSON/mesh context."""

    payload = build_nc_edit_payload(
        base_program=base_program,
        change_request=change_request,
        history=history,
        program_path=program_path,
    )
    prompt = _maybe_append_instruction(payload.prompt, extra_instructions)
    raw_output = _invoke_ollama(model=model, prompt=prompt, image_paths=[])
    parsed_json = _parse_json_block(raw_output)
    gcode = None
    if parsed_json:
        gcode = (parsed_json.get("gcode") or parsed_json.get("nc_program") or "").strip() or None
    if not gcode:
        gcode = _extract_code_block(raw_output)
    return PlanToNcResult(payload=payload, raw_output=raw_output, parsed_json=parsed_json, gcode=gcode)


def run_nc_refinement(
    *,
    model: str = _DEFAULT_NC_MODEL,
    json_path: Optional[str] = None,
    mesh_path: Optional[str] = None,
    config_path: Optional[str] = None,
    base_program: str,
    change_request: str,
    history: Optional[Sequence[Mapping[str, str]]] = None,
    strict: bool = True,
    extra_instructions: Optional[str] = None,
) -> PlanToNcResult:
    """Refine an existing NC program using the WAAM JSON + mesh context."""

    payload = build_refine_nc_payload(
        json_path=json_path,
        mesh_path=mesh_path,
        config_path=config_path,
        base_program=base_program,
        change_request=change_request,
        history=history,
        strict=strict,
        extra_instructions=extra_instructions,
    )
    # prompt = _maybe_append_instruction(payload.prompt, extra_instructions)  <-- REMOVE THIS LINE if build_refine handles it
    # Actually, let's keep it consistent. If build_refine handles it, we don't need to append again.
    # LLM Pipeline Build Refine ALREADY handles geometry_context.
    
    raw_output = _invoke_ollama(model=model, prompt=payload.prompt, image_paths=[])
    parsed_json = _parse_json_block(raw_output)
    gcode = None
    if parsed_json:
        gcode = (parsed_json.get("gcode") or parsed_json.get("nc_program") or "").strip() or None
    if not gcode:
        gcode = _extract_code_block(raw_output)
    return PlanToNcResult(payload=payload, raw_output=raw_output, parsed_json=parsed_json, gcode=gcode)


def run_quality_improvement_generation(
    *,
    model: str = _DEFAULT_NC_MODEL,
    baseline_path: Path | str,
    ops_path: Path | str,
    llm_waam_path: Path | str,
    coverage_path: Path | str,
    missing_points_path: Path | str,
    extra_instructions: str = "",
) -> PlanToNcResult:
    """Run the quality improvement workflow via Ollama."""
    
    payload = build_quality_improvement_payload(
        baseline_path=baseline_path,
        ops_path=ops_path,
        llm_waam_path=llm_waam_path,
        coverage_path=coverage_path,
        missing_points_path=missing_points_path,
        extra_instructions=extra_instructions,
    )
    
    raw_output = _invoke_ollama(model=model, prompt=payload.prompt, image_paths=[])
    parsed_json = _parse_json_block(raw_output)
    gcode = None
    if parsed_json:
        gcode = (parsed_json.get("gcode") or parsed_json.get("nc_program") or "").strip() or None
    if not gcode:
        gcode = _extract_code_block(raw_output)
        
    return PlanToNcResult(payload=payload, raw_output=raw_output, parsed_json=parsed_json, gcode=gcode)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the two-stage WAAM assistant via Ollama.")
    parser.add_argument(
        "--mode",
        choices=("planner", "nc", "json-nc", "edit", "refine"),
        default="planner",
        help="Select 'planner', 'nc', 'json-nc', 'edit', or 'refine'.",
    )
    parser.add_argument(
        "--planner-model",
        default=_DEFAULT_PLANNER_MODEL,
        help=f"Model for planner stage (default: {{_DEFAULT_PLANNER_MODEL}} or WAAM_PLANNER_MODEL env).",
    )
    parser.add_argument(
        "--nc-model",
        default=_DEFAULT_NC_MODEL,
        help=f"Model for NC stage (default: {{_DEFAULT_NC_MODEL}} or WAAM_NC_MODEL env).",
    )
    parser.add_argument("--config", help="Override WAAM config path (planner/json/refine).")
    parser.add_argument("--features", help="Override features.json path (planner).")
    parser.add_argument("--operations", help="Override waam_operations.json path (planner).")
    parser.add_argument("--planner-output", help=f"Path where planner JSON will be written (default: {DEFAULT_PLANNER_PLAN}).")
    parser.add_argument("--plan-json", help=f"Planner JSON to feed into NC stage (default: {DEFAULT_PLANNER_PLAN}).")
    parser.add_argument("--image", help="Optional reference image for NC generation.")
    parser.add_argument("--json", help="Deterministic WAAM JSON for json-nc/refine.")
    parser.add_argument("--mesh", help="Mesh context file (mesh_for_llm.txt or OBJ) for json-nc/refine.")
    parser.add_argument("--input-nc", help="Existing NC program for edit/refine.")
    parser.add_argument("--prompt", help="Change request for edit/refine.")
    parser.add_argument("--history", help="JSON history file for edit/refine (list of {role, content}).")
    parser.add_argument("--out-dir", help="Output directory for auto-named NC edits (edit/refine).")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Planner: continue even if config/features/operations are missing (they will be blank).",
    )
    parser.add_argument("--extra", help="Optional instruction appended to the active model prompt.")
    parser.add_argument("--print-prompt", action="store_true", help="Echo the prompt sent to the model.")
    parser.add_argument(
        "--save-nc",
        help="Path to save the generated NC program (defaults to stdout or auto-named for edit/refine).",
    )
    parser.add_argument("--full-spec", action="store_true", help="Planner: send full JSON spec (default uses compact summary).")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.mode == "planner":
        result = run_planner_stage(
            model=args.planner_model,
            config_path=args.config,
            features_path=args.features,
            operations_path=args.operations,
            strict=not args.allow_missing,
            extra_instructions=args.extra,
            compact=not args.full_spec,
            save_path=args.planner_output,
        )
        if args.print_prompt:
            print("===== PLANNER PROMPT BEGIN =====")
            print(result.payload.prompt)
            print("===== PLANNER PROMPT END =====")
        if result.parsed_json is not None:
            print(json.dumps(result.parsed_json, indent=2))
            if result.saved_path:
                print(f"[WAAM] Planner JSON saved to {result.saved_path}")
        else:
            print(result.raw_output or "[Empty planner response]")
        return 0

    if args.mode == "nc":
        result = run_plan_to_nc_generation(
            model=args.nc_model,
            planner_path=args.plan_json or args.planner_output,
            image_path=args.image,
            strict=not args.allow_missing,
            extra_instructions=args.extra,
        )
    elif args.mode == "json-nc":
        if not args.json or not args.mesh:
            print("[WAAM] Provide --json and --mesh for json-nc mode.")
            return 2
        result = run_json_to_nc_generation(
            model=args.nc_model,
            json_path=args.json,
            mesh_path=args.mesh,
            config_path=args.config,
            strict=not args.allow_missing,
            extra_instructions=args.extra,
        )
    elif args.mode == "edit":
        if not args.input_nc:
            print("[WAAM] Provide --input-nc for edit mode.")
            return 2
        if not args.prompt:
            print("[WAAM] Provide --prompt for edit mode.")
            return 2
        input_path = Path(args.input_nc).expanduser().resolve()
        base_program = input_path.read_text(encoding="utf-8")
        history = _load_history(args.history)
        result = run_nc_edit(
            model=args.nc_model,
            base_program=base_program,
            change_request=args.prompt,
            history=history,
            program_path=str(input_path),
            extra_instructions=args.extra,
        )
        if args.print_prompt:
            print("===== NC EDIT PROMPT BEGIN =====")
            print(result.payload.prompt)
            print("===== NC EDIT PROMPT END =====")
        if args.history:
            _append_history(history, "user", args.prompt)
            assistant_summary = _summarize_assistant_response(result.parsed_json, result.raw_output)
            _append_history(history, "assistant", assistant_summary)
            _write_history(args.history, history)
        if result.gcode:
            output_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else (get_output_dir() / "nc_files" / "nc_agent")
            output_path = Path(args.save_nc).expanduser().resolve() if args.save_nc else _next_agent_output(
                output_dir, _infer_agent_prefix(input_path)
            )
            _write_nc_file(output_path, result.gcode)
            print(f"[WAAM] NC program saved to {output_path}")
        else:
            print("[WAAM] Unable to extract NC program. Full response follows:")
            print(result.raw_output or "[Empty response]")
        if result.parsed_json is not None:
            print("===== MODEL JSON =====")
            print(json.dumps(result.parsed_json, indent=2))
        return 0
    elif args.mode == "refine":
        if not args.input_nc:
            print("[WAAM] Provide --input-nc for refine mode.")
            return 2
        if not args.prompt:
            print("[WAAM] Provide --prompt for refine mode.")
            return 2
        if not args.json or not args.mesh:
            print("[WAAM] Provide --json and --mesh for refine mode.")
            return 2
        input_path = Path(args.input_nc).expanduser().resolve()
        base_program = input_path.read_text(encoding="utf-8")
        history = _load_history(args.history)
        result = run_nc_refinement(
            model=args.nc_model,
            json_path=args.json,
            mesh_path=args.mesh,
            config_path=args.config,
            base_program=base_program,
            change_request=args.prompt,
            history=history,
            strict=not args.allow_missing,
            extra_instructions=args.extra,
        )
        if args.print_prompt:
            print("===== NC REFINE PROMPT BEGIN =====")
            print(result.payload.prompt)
            print("===== NC REFINE PROMPT END =====")
        if args.history:
            _append_history(history, "user", args.prompt)
            assistant_summary = _summarize_assistant_response(result.parsed_json, result.raw_output)
            _append_history(history, "assistant", assistant_summary)
            _write_history(args.history, history)
        if result.gcode:
            output_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else (get_output_dir() / "nc_files" / "nc_agent")
            output_path = Path(args.save_nc).expanduser().resolve() if args.save_nc else _next_agent_output(
                output_dir, _infer_agent_prefix(input_path)
            )
            _write_nc_file(output_path, result.gcode)
            print(f"[WAAM] NC program saved to {output_path}")
        else:
            print("[WAAM] Unable to extract NC program. Full response follows:")
            print(result.raw_output or "[Empty response]")
        if result.parsed_json is not None:
            print("===== MODEL JSON =====")
            print(json.dumps(result.parsed_json, indent=2))
        return 0
    else:
        print(f"[WAAM] Unsupported mode: {args.mode}")
        return 2

    if args.print_prompt:
        print("===== NC PROMPT BEGIN =====")
        print(result.payload.prompt)
        print("===== NC PROMPT END =====")
    if result.gcode:
        if args.save_nc:
            output_path = Path(args.save_nc).expanduser().resolve()
            _write_nc_file(output_path, result.gcode)
            print(f"[WAAM] NC program saved to {output_path}")
        else:
            print("===== GENERATED NC PROGRAM =====")
            print(result.gcode)
    else:
        print("[WAAM] Unable to extract NC program. Full response follows:")
        print(result.raw_output or "[Empty response]")

    if result.parsed_json is not None:
        print("===== MODEL JSON =====")
        print(json.dumps(result.parsed_json, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
