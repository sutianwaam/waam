"""Smoke tests for tools/waam_regress.py and tools/plan_regress.py.

Verifies both directions: pass on equivalent artifacts, fail (exit 2) on a
deliberately mutated copy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import plan_regress  # noqa: E402
import waam_regress  # noqa: E402


def test_waam_regress_passes_on_identical_file(golden_baseline_nc_path):
    problems = waam_regress.compare_nc_files(golden_baseline_nc_path, golden_baseline_nc_path)
    assert problems == []


def test_waam_regress_fails_on_shifted_coordinate(tmp_path, golden_baseline_nc_path):
    lines = golden_baseline_nc_path.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("G1 X") and "Y" in line:
            lines[idx] = line.replace("G1 X", "G1 X999", 1)
            break
    mutated = tmp_path / "mutated.nc"
    mutated.write_text("\n".join(lines), encoding="utf-8")

    problems = waam_regress.compare_nc_files(mutated, golden_baseline_nc_path)
    assert problems


def test_plan_regress_passes_on_identical_file(golden_plan_path):
    plan = json.loads(golden_plan_path.read_text(encoding="utf-8"))
    problems = plan_regress.compare_plans(plan, plan)
    assert problems == []


def test_plan_regress_fails_on_shifted_path_point(golden_plan_path):
    plan = json.loads(golden_plan_path.read_text(encoding="utf-8"))
    mutated = json.loads(golden_plan_path.read_text(encoding="utf-8"))
    paths = mutated["llm_context"]["waam_structured"]["paths"]
    first_id = next(iter(paths))
    paths[first_id]["points_xyz"][0][0] += 5.0

    problems = plan_regress.compare_plans(mutated, plan)
    assert problems
