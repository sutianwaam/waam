"""Verifies the core OpenClaw workflow: edit Waam_tech/experimental/, click
"Run Pipeline + Experimental NC", get a new NC that reflects the new logic.

The production entry point (`RuntimeActiveExperimentalPlanner.export_nc`)
evicts any previously-imported `planner.py`/`helpers.py` modules from
`sys.modules` before re-importing them from disk on every call (see
`Waam_tech/experimental/runtime/loader.py::_load_active_runtime_planner`).
That is what lets a long-running FreeCAD session pick up an edit someone
(or an agent) just made to the experimental planner without a restart.

This test exercises the real `RuntimeActiveExperimentalPlanner` class --
not a bypass -- but points it at an isolated `experimental_root` (a
constructor override the class already supports) so it never reads or
writes the contributor's real `Waam_tech/experimental/` tree: no ACTIVE_PART.json,
runtime/parts/, runtime/archive/, or notes.md in the real repo are touched.
"""

from __future__ import annotations

import json
import shutil

from Waam_tech.experimental.base import ExperimentalPlannerContext
from Waam_tech.experimental.runtime.loader import RuntimeActiveExperimentalPlanner

_EDITED_PLANNER_SOURCE = """from __future__ import annotations

from .helpers import append_iteration_note, copy_baseline_to_experimental, load_manifest


class RuntimeActivePlanner:
    key = "runtime_active"
    label = "Active runtime experimental NC"
    output_filename = "waam_baseline_experimental.nc"

    def export_nc(self, context):
        append_iteration_note(
            context,
            heading="test edit",
            lines=["- change: injected a marker comment line"],
        )
        target = copy_baseline_to_experimental(context)
        text = target.read_text(encoding="utf-8")
        text = text.replace("%\\n", "%\\n(OPENCLAW MARKER: hot-reload test)\\n", 1)
        target.write_text(text, encoding="utf-8")
        return target
"""


def _make_context(tmp_path, golden_plan_path, golden_baseline_nc_path, run_id):
    out_dir = tmp_path / "output"
    plan_path = tmp_path / "plan.json"
    baseline_nc = tmp_path / "waam_baseline.nc"
    features_path = tmp_path / "features.json"
    if not plan_path.is_file():
        shutil.copyfile(golden_plan_path, plan_path)
    if not baseline_nc.is_file():
        shutil.copyfile(golden_baseline_nc_path, baseline_nc)
    if not features_path.is_file():
        features_path.write_text(json.dumps({"metadata": {"work_obj": "testpart"}, "features": []}))
    return ExperimentalPlannerContext(
        output_dir=out_dir,
        plan_path=plan_path,
        features_path=features_path,
        baseline_nc_path=baseline_nc,
        run_id=run_id,
    )


def test_editing_experimental_planner_is_picked_up_on_next_export(
    tmp_path, golden_plan_path, golden_baseline_nc_path
):
    experimental_root = tmp_path / "experimental_root"
    planner = RuntimeActiveExperimentalPlanner(experimental_root=experimental_root)

    # First run: seeds a clean-template planner (just copies baseline -> experimental).
    context1 = _make_context(tmp_path, golden_plan_path, golden_baseline_nc_path, "run-1")
    result1 = planner.export_nc(context1)
    assert "OPENCLAW MARKER" not in result1.read_text(encoding="utf-8")

    # Simulate an OpenClaw edit landing on disk, in the experimental-only edit surface.
    active_planner_path = experimental_root / "runtime" / "active" / "planner.py"
    assert active_planner_path.is_file()
    active_planner_path.write_text(_EDITED_PLANNER_SOURCE, encoding="utf-8")

    # Second run: SAME planner instance/process (as a long-running FreeCAD
    # session would be) must pick up the edited logic, not a cached import.
    context2 = _make_context(tmp_path, golden_plan_path, golden_baseline_nc_path, "run-2")
    result2 = planner.export_nc(context2)
    assert "OPENCLAW MARKER" in result2.read_text(encoding="utf-8")

    # The baseline NC referenced by the context is never modified.
    assert context2.baseline_nc_path.read_bytes() == golden_baseline_nc_path.read_bytes()


def test_experimental_root_override_never_touches_real_repo_tree(
    tmp_path, golden_plan_path, golden_baseline_nc_path, repo_root
):
    real_active_notes = repo_root / "Waam_tech" / "experimental" / "runtime" / "active" / "notes.md"
    before = real_active_notes.read_bytes() if real_active_notes.is_file() else None

    experimental_root = tmp_path / "experimental_root"
    planner = RuntimeActiveExperimentalPlanner(experimental_root=experimental_root)
    context = _make_context(tmp_path, golden_plan_path, golden_baseline_nc_path, "run-isolated")
    planner.export_nc(context)

    after = real_active_notes.read_bytes() if real_active_notes.is_file() else None
    assert before == after
