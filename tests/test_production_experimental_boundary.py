"""The baseline pipeline and the experimental runtime must stay on separate rails.

AGENTS.md and README both state that the agent drives only the experimental
NC branch, and that experimental output must never silently overwrite
waam_baseline.nc. These checks make that boundary machine-checkable instead
of just documented.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from Waam_tech.experimental.registry import get_experimental_planner
from Waam_tech.waamgen import pipeline as waamgen_pipeline
from Waam_tech.waamgen.pipeline import generate_nc


def test_waamgen_pipeline_module_has_no_experimental_coupling():
    source = inspect.getsource(waamgen_pipeline)
    assert "experimental" not in source.lower()


def test_experimental_planner_output_filename_is_never_the_baseline_name():
    planner = get_experimental_planner("runtime_active")
    assert planner is not None
    assert planner.output_filename == "waam_baseline_experimental.nc"
    assert planner.output_filename != "waam_baseline.nc"


def test_baseline_generate_nc_only_writes_requested_output_and_attempt_dir(tmp_path, golden_plan_path):
    sentinel_active_dir = tmp_path / "would_be_experimental_active"
    out_path = tmp_path / "baseline_output" / "waam_baseline.nc"
    attempt_dir = tmp_path / "attempts"

    generate_nc(golden_plan_path, output_path=out_path, attempt_dir=attempt_dir)

    assert out_path.is_file()
    assert not sentinel_active_dir.exists()
    # Nothing was written outside of the explicit output path and attempt dir.
    written_files = {p for p in tmp_path.rglob("*") if p.is_file()}
    allowed_roots = (out_path, attempt_dir)
    for path in written_files:
        assert any(_is_within(path, root) for root in allowed_roots), path


def _is_within(path: Path, root: Path) -> bool:
    if root.is_file():
        return path == root
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
