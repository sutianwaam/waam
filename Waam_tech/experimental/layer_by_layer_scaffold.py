from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

from .base import ExperimentalPlannerContext
from ..waamgen import generate_nc as waamgen_generate


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "current-part"


def _load_json(path: Path) -> Mapping[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def derive_part_key(features: Mapping[str, object]) -> str:
    metadata = features.get("metadata") or {}
    work_obj = metadata.get("work_obj")
    if isinstance(work_obj, str) and work_obj.strip():
        return _slugify(work_obj)
    return "current-part"


def derive_part_key_from_path(features_path: Path) -> str:
    return derive_part_key(_load_json(features_path))


def _template_path() -> Path:
    return Path(__file__).resolve().parent / "knowledge" / "part-lessons" / "_template.md"


def write_scaffold_bundle(
    context: ExperimentalPlannerContext,
    exp_nc: Path,
    *,
    planner_key: str = "layer_by_layer",
    planner_module: str = "Waam_tech/experimental/layer_by_layer_scaffold.py",
    next_goal: str = (
        "Create the next part-specific experimental improvement from the clean scaffold, "
        "not from stale hard-coded legacy part scripts."
    ),
) -> None:
    scaffold_dir = context.output_dir / "experimental_scaffold"
    scaffold_dir.mkdir(parents=True, exist_ok=True)

    features = _load_json(context.features_path)
    part_key = derive_part_key(features)

    template = _template_path().read_text(encoding="utf-8")
    lesson_text = (
        template.replace("<part-name>", part_key.replace("-", " ").title())
        .replace("<slug-name>", part_key)
    )
    lesson_text = lesson_text.replace(
        "- geometry family: <thin-wall / bulk / multi-hole / topology-changing / curved / etc.>",
        f"- geometry family: {context.part_family or 'pending layer-by-layer classification'}",
    )
    lesson_text = lesson_text.replace(
        "- status: <active / archived / promoted / diagnostic>",
        "- status: active",
    )
    lesson_text = lesson_text.replace(
        "- which experimental planner / module is being used?",
        "- which experimental planner / module is being used?\n"
        f"- `{planner_module}`",
    )
    lesson_text = lesson_text.replace(
        "- which files were created or modified?",
        "- which files were created or modified?\n"
        f"- experimental NC: `{exp_nc}`\n"
        f"- fused operations: `{context.fused_operations_path}`\n"
        f"- features: `{context.features_path}`",
    )
    lesson_text = lesson_text.replace(
        "- what should a future model read first?",
        "- what should a future model read first?\n"
        "- `Waam_tech/experimental/knowledge/README.md`\n"
        "- `Waam_tech/experimental/knowledge/planner-patterns.md`\n"
        "- `Waam_tech/experimental/knowledge/failure-modes.md`\n"
        "- `Waam_tech/experimental/knowledge/waam-toolpath-key-points.md`",
    )

    lesson_path = scaffold_dir / f"{part_key}_lesson.md"
    lesson_path.write_text(lesson_text, encoding="utf-8")

    scaffold_payload = {
        "part_key": part_key,
        "part_family": context.part_family or "",
        "planner": planner_key,
        "planner_basis": "layer-by-layer material deposition",
        "artifacts": {
            "experimental_nc": str(exp_nc),
            "fused_operations": str(context.fused_operations_path),
            "features": str(context.features_path),
        },
        "recommended_reads": [
            "Waam_tech/experimental/knowledge/README.md",
            "Waam_tech/experimental/knowledge/planner-patterns.md",
            "Waam_tech/experimental/knowledge/failure-modes.md",
            "Waam_tech/experimental/knowledge/waam-toolpath-key-points.md",
        ],
        "next_goal": next_goal,
    }
    context_path = scaffold_dir / f"{part_key}_context.json"
    context_path.write_text(json.dumps(scaffold_payload, indent=2), encoding="utf-8")


class LayerByLayerExperimentalPlanner:
    key = "layer_by_layer"
    label = "Layer-by-layer experimental NC scaffold"
    output_filename = "waam_baseline_experimental.nc"
    scaffold_dirname = "experimental_scaffold"

    def export_nc(self, context: ExperimentalPlannerContext) -> Path:
        nc_dir = context.output_dir / "nc_files"
        nc_dir.mkdir(parents=True, exist_ok=True)
        target = nc_dir / self.output_filename
        waamgen_generate(
            context.fused_operations_path,
            output_path=target,
            max_iters=1,
            geom_tol=0.8,
            plot=False,
        )
        write_scaffold_bundle(context, target)
        return target
