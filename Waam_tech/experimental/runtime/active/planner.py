from __future__ import annotations

from pathlib import Path

from .helpers import (
    append_iteration_note,
    export_constant_section_two_hole_nc,
    load_manifest,
)


class RuntimeActivePlanner:
    key = "runtime_active"
    label = "Active runtime experimental NC"
    output_filename = "waam_baseline_experimental.nc"

    def export_nc(self, context):
        manifest = load_manifest()
        goal = manifest.get("goal") or "No experimental goal recorded."
        part_key = str(context.part_key or manifest.get("part_key") or "current-part").lower()
        part_label = str(context.part_label or manifest.get("part_label") or part_key)

        target = export_constant_section_two_hole_nc(context)

        append_iteration_note(
            context,
            heading="Experimental constant-section runtime export",
            lines=[
                f"- part: {part_key}",
                f"- label: {part_label}",
                f"- goal: {goal}",
                "- strategy: reuse a constant cross-section template layer and repeat it upward with 2-hole preservation",
                "- intent: avoid fragmented topology-sensitive starts for vertically stable two-hole parts",
            ],
        )
        return Path(target)
