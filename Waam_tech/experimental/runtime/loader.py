from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..base import ExperimentalPlannerContext
from ..layer_by_layer_scaffold import write_scaffold_bundle


_ACTIVE_PLANNER_REL = "runtime/active/planner.py"
_ACTIVE_HELPERS_REL = "runtime/active/helpers.py"
_ACTIVE_MANIFEST_REL = "runtime/active/manifest.json"
_ACTIVE_NOTES_REL = "runtime/active/notes.md"
_ACTIVE_PROMPT_CONTEXT_REL = "runtime/active/prompt_context.json"
_ALLOWED_FILES = [
    "Waam_tech/experimental/runtime/active/planner.py",
    "Waam_tech/experimental/runtime/active/helpers.py",
    "Waam_tech/experimental/runtime/active/notes.md",
    "Waam_tech/experimental/runtime/active/manifest.json",
    "Waam_tech/experimental/runtime/active/prompt_context.json",
]
_DEFAULT_GOAL = "Create the next part-specific experimental improvement from the active runtime sandbox."
_HOLE_FEATURE_TYPES = {"hole", "through_hole", "cylindrical_hole"}
_POCKET_FEATURE_TYPES = {"planar_pocket_floor"}
_FREEFORM_FEATURE_TYPES = {"freeform_area"}


def _experimental_root(experimental_root: Path | str | None = None) -> Path:
    if experimental_root is not None:
        return Path(experimental_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _paths(experimental_root: Path | str | None = None) -> dict[str, Path]:
    root = _experimental_root(experimental_root)
    runtime_root = root / "runtime"
    active_root = runtime_root / "active"
    return {
        "experimental_root": root,
        "runtime_root": runtime_root,
        "active_root": active_root,
        "archive_root": runtime_root / "archive",
        "active_part": root / "ACTIVE_PART.json",
        "contract": root / "CONTRACT.md",
        "planner": active_root / "planner.py",
        "helpers": active_root / "helpers.py",
        "manifest": active_root / "manifest.json",
        "notes": active_root / "notes.md",
        "prompt_context": active_root / "prompt_context.json",
        "active_init": active_root / "__init__.py",
        "runtime_init": runtime_root / "__init__.py",
    }


def _read_json(path: Path, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return dict(default or {})
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _timestamp_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return _timestamp_slug().lower()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "current-part"


def _load_features(features_path: Path) -> dict[str, Any]:
    return _read_json(features_path, {})


def _features_signature(features_path: Path) -> str:
    if not features_path.is_file():
        return ""
    return hashlib.sha1(features_path.read_bytes()).hexdigest()[:12]


def _part_label_from_active_state(active_state: Mapping[str, Any] | None) -> str:
    if not active_state:
        return ""
    for key in ("active_part_label", "part_label", "active_part"):
        value = active_state.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).stem.strip()
    return ""


def infer_part_family_from_features_doc(features: Mapping[str, Any]) -> tuple[str, list[str]]:
    feat_list = list(features.get("features") or [])
    metadata = features.get("metadata") or {}
    mesh_analysis = metadata.get("mesh_analysis") or {}
    thin_wall_samples = list((mesh_analysis.get("thin_walls") or {}).get("samples") or [])
    hole_count = 0
    pocket_count = 0
    freeform_count = 0
    thin_wall_risk = False
    through_hole_count = 0
    for feat in feat_list:
        if not isinstance(feat, Mapping):
            continue
        ftype = str(feat.get("type") or "").strip().lower()
        if ftype in _HOLE_FEATURE_TYPES:
            hole_count += 1
            if ftype == "through_hole":
                through_hole_count += 1
        elif ftype in _POCKET_FEATURE_TYPES:
            pocket_count += 1
        elif ftype in _FREEFORM_FEATURE_TYPES:
            freeform_count += 1
        risk_tags = feat.get("risk_tags") or []
        if isinstance(risk_tags, list) and "thin_wall_neighbor" in risk_tags:
            thin_wall_risk = True

    thin_wall_signal = bool(thin_wall_samples) or thin_wall_risk
    void_feature_count = hole_count + pocket_count

    signals: list[str] = []
    if thin_wall_signal:
        signals.append("thin_wall_signal")
    if hole_count:
        signals.append("hole_features")
    if through_hole_count:
        signals.append("through_holes")
    if pocket_count:
        signals.append("pocket_features")
    if freeform_count:
        signals.append("freeform_regions")

    if thin_wall_signal and void_feature_count:
        return "hybrid_mixed", signals
    if thin_wall_signal:
        return "thin_wall_frame", signals
    if hole_count >= 3 and pocket_count == 0 and freeform_count == 0:
        return "multi_hole_plate", signals
    if void_feature_count:
        return "topology_transition_void_protection", signals
    if freeform_count:
        return "hybrid_mixed", signals
    return "bulk_fill", signals


def infer_part_family_from_features_path(features_path: Path) -> tuple[str, list[str]]:
    return infer_part_family_from_features_doc(_load_features(features_path))


def derive_part_identity_from_features_path(
    features_path: Path,
    *,
    step_path: Path | None = None,
    active_state: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    features = _load_features(features_path)
    metadata = features.get("metadata") or {}
    work_obj = metadata.get("work_obj")
    label = ""
    if isinstance(work_obj, str) and work_obj.strip():
        label = Path(work_obj).stem.strip()
    if not label and step_path is not None:
        label = step_path.stem.strip()
    if not label and active_state:
        label = _part_label_from_active_state(active_state)
    if not label:
        label = "Current Part"
    return label, _slugify(label)


def derive_part_profile_from_features_path(
    features_path: Path,
    *,
    step_path: Path | None = None,
    active_state: Mapping[str, Any] | None = None,
) -> tuple[str, str, str, list[str]]:
    label, key = derive_part_identity_from_features_path(
        features_path,
        step_path=step_path,
        active_state=active_state,
    )
    family, signals = infer_part_family_from_features_path(features_path)
    return label, key, family, signals


def load_active_part_state(*, experimental_root: Path | str | None = None) -> dict[str, Any]:
    return _read_json(_paths(experimental_root)["active_part"], {})


def load_active_manifest(*, experimental_root: Path | str | None = None) -> dict[str, Any]:
    return _read_json(_paths(experimental_root)["manifest"], {})


def _ensure_runtime_layout(paths: Mapping[str, Path]) -> None:
    paths["active_root"].mkdir(parents=True, exist_ok=True)
    paths["archive_root"].mkdir(parents=True, exist_ok=True)
    for key in ("runtime_init", "active_init"):
        if not paths[key].exists():
            paths[key].write_text("", encoding="utf-8")


def _active_goal(active_state: Mapping[str, Any], current_manifest: Mapping[str, Any]) -> str:
    for key in ("goal", "notes"):
        note = active_state.get(key)
        if isinstance(note, str) and note.strip():
            return note.strip()
    goal = current_manifest.get("goal")
    if isinstance(goal, str) and goal.strip():
        return goal.strip()
    return _DEFAULT_GOAL


def _expected_experimental_nc(context: ExperimentalPlannerContext) -> Path:
    return context.output_dir / "nc_files" / "waam_baseline_experimental.nc"


def _default_planner_source() -> str:
    return """from __future__ import annotations

from .helpers import append_iteration_note, copy_baseline_to_experimental, load_manifest


class RuntimeActivePlanner:
    key = "runtime_active"
    label = "Active runtime experimental NC"
    output_filename = "waam_baseline_experimental.nc"

    def export_nc(self, context):
        manifest = load_manifest()
        goal = manifest.get("goal") or "No experimental goal recorded."
        append_iteration_note(
            context,
            heading="Scaffold export",
            lines=[
                f"- part: {context.part_key or manifest.get('part_key') or 'current-part'}",
                f"- goal: {goal}",
                "- change: copied the current waam_baseline.nc into waam_baseline_experimental.nc",
                "- expected improvement: provide a safe editable starting point for the next iteration",
            ],
        )
        return copy_baseline_to_experimental(context)
"""


def _default_helpers_source() -> str:
    return """from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from Waam_tech.waamgen import generate_nc as waamgen_generate

ACTIVE_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ACTIVE_ROOT / "manifest.json"
NOTES_PATH = ACTIVE_ROOT / "notes.md"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_manifest():
    if not MANIFEST_PATH.is_file():
        return {}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(payload) -> None:
    MANIFEST_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def baseline_output_path(context) -> Path:
    if context.baseline_nc_path:
        return Path(context.baseline_nc_path)
    return context.output_dir / "nc_files" / "waam_baseline.nc"


def experimental_output_path(context) -> Path:
    target = context.output_dir / "nc_files" / "waam_baseline_experimental.nc"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def read_baseline_text(context) -> str:
    baseline = baseline_output_path(context)
    if not baseline.is_file():
        return ""
    return baseline.read_text(encoding="utf-8")


def read_baseline_lines(context):
    text = read_baseline_text(context)
    if not text:
        return []
    return text.splitlines()


def write_experimental_text(context, text: str) -> Path:
    target = experimental_output_path(context)
    target.write_text(text, encoding="utf-8")
    return target


def write_experimental_lines(context, lines) -> Path:
    text = "\\n".join(lines)
    if lines:
        text += "\\n"
    return write_experimental_text(context, text)


def append_iteration_note(context, *, heading: str, lines) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not NOTES_PATH.exists():
        NOTES_PATH.write_text("# Active Experiment Notes\\n\\n", encoding="utf-8")
    with NOTES_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"## {heading} ({_timestamp()})\\n")
        for line in lines:
            handle.write(f"{line}\\n")
        handle.write("\\n")


def default_export_nc(context, *, max_iters: int = 1, geom_tol: float = 0.8) -> Path:
    target = experimental_output_path(context)
    waamgen_generate(
        context.fused_operations_path,
        output_path=target,
        max_iters=max_iters,
        geom_tol=geom_tol,
        plot=False,
    )
    return target


def copy_baseline_to_experimental(context) -> Path:
    baseline = baseline_output_path(context)
    target = experimental_output_path(context)
    if baseline.is_file():
        shutil.copyfile(baseline, target)
        return target
    return default_export_nc(context)
"""


def _default_notes_text(part_label: str, goal: str) -> str:
    return (
        f"# Active Experiment Notes: {part_label}\n\n"
        "## Goal\n"
        f"{goal}\n\n"
        "## Contract\n"
        "- Edit only `Waam_tech/experimental/`.\n"
        "- Keep `waam_baseline.nc` as the control output.\n"
        "- Write candidate changes into `waam_baseline_experimental.nc`.\n\n"
        "## Current Hypothesis\n"
        "- Start from the baseline NC copy and add only part-specific experimental logic.\n"
        "- Keep changes local to `runtime/active/planner.py` and `runtime/active/helpers.py`.\n"
    )


def _archive_active_workspace(
    paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
    active_state: Mapping[str, Any],
) -> Path | None:
    part_key = str(manifest.get("part_key") or "").strip()
    if not part_key:
        return None
    archive_dir = paths["archive_root"] / part_key / _timestamp_slug()
    archive_dir.mkdir(parents=True, exist_ok=True)
    for source in (
        paths["planner"],
        paths["helpers"],
        paths["manifest"],
        paths["notes"],
        paths["prompt_context"],
    ):
        if source.is_file():
            shutil.copy2(source, archive_dir / source.name)
    if active_state:
        _write_json(archive_dir / "ACTIVE_PART.json", active_state)
    return archive_dir


def _build_manifest(
    context: ExperimentalPlannerContext,
    *,
    part_label: str,
    part_key: str,
    part_family: str,
    part_family_signals: list[str],
    goal: str,
    iteration: int,
    current_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    experimental_nc = _expected_experimental_nc(context)
    created_at = current_manifest.get("created_at") or _timestamp_iso()
    return {
        "part_key": part_key,
        "part_label": part_label,
        "part_family": part_family,
        "part_family_signals": list(part_family_signals),
        "status": str(current_manifest.get("status") or "iterating"),
        "iteration": int(current_manifest.get("iteration") or iteration),
        "planner_basis": "layer-by-layer material deposition",
        "active_planner": _ACTIVE_PLANNER_REL,
        "runtime_planner_path": _ACTIVE_PLANNER_REL,
        "planner_module": "Waam_tech/experimental/runtime/active/planner.py",
        "source_step": str(context.step_path) if context.step_path else "",
        "features_path": str(context.features_path),
        "features_signature": _features_signature(context.features_path),
        "fused_operations_path": str(context.fused_operations_path),
        "baseline_nc": str(context.baseline_nc_path) if context.baseline_nc_path else "",
        "experimental_nc": str(experimental_nc),
        "allowed_files": list(_ALLOWED_FILES),
        "goal": goal,
        "last_review_input": str(current_manifest.get("last_review_input") or ""),
        "run_id": context.run_id or new_run_id(),
        "created_at": created_at,
        "updated_at": _timestamp_iso(),
    }


def _build_prompt_context(
    context: ExperimentalPlannerContext,
    *,
    part_label: str,
    part_key: str,
    part_family: str,
    manifest: Mapping[str, Any],
    active_state: Mapping[str, Any],
    paths: Mapping[str, Path],
) -> dict[str, Any]:
    return {
        "contract_path": "Waam_tech/experimental/CONTRACT.md",
        "active_part": active_state,
        "part_label": part_label,
        "part_key": part_key,
        "part_family": part_family,
        "part_family_signals": list(manifest.get("part_family_signals") or []),
        "planner_basis": "layer-by-layer material deposition",
        "allowed_edit_root": "Waam_tech/experimental/",
        "allowed_files": list(_ALLOWED_FILES),
        "artifacts": {
            "step_path": str(context.step_path) if context.step_path else "",
            "features_path": str(context.features_path),
            "fused_operations_path": str(context.fused_operations_path),
            "baseline_nc_path": str(context.baseline_nc_path) if context.baseline_nc_path else "",
            "experimental_nc_path": str(_expected_experimental_nc(context)),
        },
        "active_runtime_files": {
            "planner": str(paths["planner"]),
            "helpers": str(paths["helpers"]),
            "manifest": str(paths["manifest"]),
            "notes": str(paths["notes"]),
        },
        "recommended_reads": [
            "Waam_tech/experimental/CONTRACT.md",
            "Waam_tech/experimental/knowledge/README.md",
            "Waam_tech/experimental/knowledge/planner-patterns.md",
            "Waam_tech/experimental/knowledge/failure-modes.md",
            "Waam_tech/experimental/knowledge/waam-toolpath-key-points.md",
        ],
        "manifest": manifest,
        "next_goal": "Edit runtime/active/planner.py and helpers.py for this part-specific iteration, then rerun FreeCAD.",
    }


def _sync_active_part(
    paths: Mapping[str, Path],
    *,
    part_label: str,
    part_key: str,
    part_family: str,
    part_family_signals: list[str],
    goal: str,
    current_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    active_payload = {
        "active_part_label": part_label,
        "active_part_key": part_key,
        "active_part_family": part_family,
        "active_part_family_signals": list(part_family_signals),
        "runtime_planner_path": "runtime/active/planner.py",
        "status": str(current_manifest.get("status") or "iterating"),
        "goal": goal,
    }
    _write_json(paths["active_part"], active_payload)
    return active_payload


def ensure_active_runtime(
    context: ExperimentalPlannerContext,
    *,
    experimental_root: Path | str | None = None,
) -> dict[str, Any]:
    paths = _paths(experimental_root)
    _ensure_runtime_layout(paths)
    active_state = load_active_part_state(experimental_root=experimental_root)
    part_label, part_key, part_family, part_family_signals = derive_part_profile_from_features_path(
        context.features_path,
        step_path=context.step_path,
        active_state=active_state,
    )
    if not context.part_key:
        context.part_key = part_key
    if not context.part_label:
        context.part_label = part_label
    if not context.part_family:
        context.part_family = part_family
    if context.part_family_signals is None:
        context.part_family_signals = list(part_family_signals)

    current_manifest = load_active_manifest(experimental_root=experimental_root)
    goal = _active_goal(active_state, current_manifest)

    needs_reset = (
        not paths["manifest"].is_file()
        or not paths["planner"].is_file()
        or not paths["helpers"].is_file()
        or current_manifest.get("part_key") != part_key
        or str(current_manifest.get("runtime_planner_path") or current_manifest.get("active_planner") or "") not in {"", _ACTIVE_PLANNER_REL}
    )

    if needs_reset and current_manifest:
        _archive_active_workspace(paths, current_manifest, active_state)

    if needs_reset:
        paths["planner"].write_text(_default_planner_source(), encoding="utf-8")
        paths["helpers"].write_text(_default_helpers_source(), encoding="utf-8")
        paths["notes"].write_text(_default_notes_text(part_label, goal), encoding="utf-8")
        current_manifest = {}
    elif not paths["notes"].exists():
        paths["notes"].write_text(_default_notes_text(part_label, goal), encoding="utf-8")

    manifest = _build_manifest(
        context,
        part_label=part_label,
        part_key=part_key,
        part_family=part_family,
        part_family_signals=part_family_signals,
        goal=goal,
        iteration=1,
        current_manifest=current_manifest,
    )
    active_state = _sync_active_part(
        paths,
        part_label=part_label,
        part_key=part_key,
        part_family=part_family,
        part_family_signals=part_family_signals,
        goal=goal,
        current_manifest=manifest,
    )
    _write_json(paths["manifest"], manifest)
    _write_json(
        paths["prompt_context"],
        _build_prompt_context(
            context,
            part_label=part_label,
            part_key=part_key,
            part_family=part_family,
            manifest=manifest,
            active_state=active_state,
            paths=paths,
        ),
    )
    write_scaffold_bundle(
        context,
        _expected_experimental_nc(context),
        planner_key="runtime_active",
        planner_module="Waam_tech/experimental/runtime/active/planner.py",
        next_goal="Edit `Waam_tech/experimental/runtime/active/planner.py` and `helpers.py`, then rerun the experimental NC button.",
    )
    return manifest


def _load_module_from_path(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load experimental runtime module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_active_runtime_planner(
    context: ExperimentalPlannerContext,
    *,
    experimental_root: Path | str | None = None,
):
    paths = _paths(experimental_root)
    manifest = load_active_manifest(experimental_root=experimental_root)
    planner_rel = str(manifest.get("runtime_planner_path") or manifest.get("active_planner") or _ACTIVE_PLANNER_REL)
    planner_path = (paths["experimental_root"] / planner_rel).resolve()
    active_root = paths["active_root"].resolve()
    if active_root not in planner_path.parents:
        raise RuntimeError(
            "Experimental planner selection was blocked because the active planner is outside "
            "Waam_tech/experimental/runtime/active/."
        )
    if manifest.get("part_key") != context.part_key:
        raise RuntimeError(
            f"Experimental planner mismatch: active runtime planner is for '{manifest.get('part_key')}', "
            f"but the current part is '{context.part_key}'."
        )
    package_name = f"_waam_runtime_active_{hash(str(active_root)) & 0xFFFFFFFF:x}"
    package = types.ModuleType(package_name)
    package.__path__ = [str(active_root)]
    sys.modules[package_name] = package
    sys.modules.pop(f"{package_name}.helpers", None)
    sys.modules.pop(f"{package_name}.planner", None)
    _load_module_from_path(f"{package_name}.helpers", paths["helpers"])
    module = _load_module_from_path(f"{package_name}.planner", paths["planner"])
    if hasattr(module, "build_planner"):
        return module.build_planner()
    planner_cls = getattr(module, "RuntimeActivePlanner", None)
    if planner_cls is not None:
        return planner_cls()
    planner = getattr(module, "planner", None)
    if planner is not None:
        return planner
    raise RuntimeError("Runtime active planner.py must define RuntimeActivePlanner, planner, or build_planner().")


def finalize_runtime_export(
    context: ExperimentalPlannerContext,
    target: Path,
    *,
    experimental_root: Path | str | None = None,
) -> dict[str, Any]:
    paths = _paths(experimental_root)
    manifest = load_active_manifest(experimental_root=experimental_root)
    manifest["experimental_nc"] = str(target)
    manifest["last_exported_at"] = _timestamp_iso()
    manifest["run_id"] = context.run_id or manifest.get("run_id") or new_run_id()
    _write_json(paths["manifest"], manifest)
    active_state = load_active_part_state(experimental_root=experimental_root)
    _write_json(
        paths["prompt_context"],
        _build_prompt_context(
            context,
            part_label=str(manifest.get("part_label") or context.part_label or context.part_key),
            part_key=str(manifest.get("part_key") or context.part_key),
            part_family=str(manifest.get("part_family") or context.part_family or "bulk_fill"),
            manifest=manifest,
            active_state=active_state,
            paths=paths,
        ),
    )
    write_scaffold_bundle(
        context,
        target,
        planner_key="runtime_active",
        planner_module="Waam_tech/experimental/runtime/active/planner.py",
        next_goal="Compare waam_baseline.nc and waam_baseline_experimental.nc, then refine runtime/active/planner.py for the next iteration.",
    )
    return manifest


class RuntimeActiveExperimentalPlanner:
    key = "runtime_active"
    label = "Active runtime experimental NC"
    output_filename = "waam_baseline_experimental.nc"
    scaffold_dirname = "experimental_scaffold"

    def __init__(self, *, experimental_root: Path | str | None = None) -> None:
        self._experimental_root = experimental_root

    def export_nc(self, context: ExperimentalPlannerContext) -> Path:
        manifest = ensure_active_runtime(context, experimental_root=self._experimental_root)
        context.part_key = str(manifest.get("part_key") or context.part_key)
        context.part_label = str(manifest.get("part_label") or context.part_label)
        context.part_family = str(manifest.get("part_family") or context.part_family)
        if context.part_family_signals is None:
            signals = manifest.get("part_family_signals") or []
            context.part_family_signals = list(signals) if isinstance(signals, list) else []
        planner = _load_active_runtime_planner(context, experimental_root=self._experimental_root)
        target = Path(planner.export_nc(context))
        finalize_runtime_export(context, target, experimental_root=self._experimental_root)
        return target
