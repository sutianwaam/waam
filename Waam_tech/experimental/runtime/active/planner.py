from __future__ import annotations

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
