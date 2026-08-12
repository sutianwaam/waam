"""Golden-artifact regression tests: run WITHOUT FreeCAD.

These freeze a known-good (waam_slice_plan.json -> NC) pair under
tests/fixtures/golden/ and regenerate from the frozen plan on every run.
A byte-for-byte mismatch here means the deterministic NC generator changed
behavior; use tools/waam_regress.py to see whether the change is a real
regression or just a benign header/formatting difference.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

from Waam_tech.experimental.base import ExperimentalPlannerContext
from Waam_tech.waamgen.pipeline import generate_nc

EXPERIMENTAL_PLANNER_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "golden" / "experimental_planner"


def _load_frozen_experimental_planner_module():
    # `Waam_tech/experimental/runtime/active/{planner,helpers}.py` is a live
    # mirror of whichever part was last exported through the experimental
    # pipeline (see Waam_tech/experimental/runtime/loader.py) -- it changes
    # every time someone clicks "Run Pipeline + Experimental NC" for a
    # different part. Importing it directly here previously made this test
    # depend on unrelated, unversioned live state (it broke the moment a
    # different part was run). Instead, load a frozen copy of the exact
    # planner/helpers pair that produced the golden fixture below, the same
    # way the real loader dynamically loads a part workspace: as a synthetic
    # package so `planner.py`'s `from .helpers import ...` resolves.
    package_name = "_waam_test_frozen_experimental_planner"
    package = types.ModuleType(package_name)
    package.__path__ = [str(EXPERIMENTAL_PLANNER_FIXTURE_DIR)]
    sys.modules[package_name] = package

    def _load(submodule_name):
        spec = importlib.util.spec_from_file_location(
            f"{package_name}.{submodule_name}",
            EXPERIMENTAL_PLANNER_FIXTURE_DIR / f"{submodule_name}.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    helpers_module = _load("helpers")
    planner_module = _load("planner")
    return planner_module, helpers_module


def test_baseline_nc_matches_golden(tmp_path, golden_plan_path, golden_baseline_nc_path):
    out_path = tmp_path / "candidate_baseline.nc"
    generate_nc(
        golden_plan_path,
        output_path=out_path,
        max_iters=1,
        geom_tol=0.8,
        plot=False,
        attempt_dir=tmp_path / "attempts",
    )
    assert out_path.read_bytes() == golden_baseline_nc_path.read_bytes()


def test_experimental_nc_matches_golden(tmp_path, golden_plan_path, golden_experimental_nc_path):
    # Exercises the experimental runtime's pure transform + NC emission
    # directly, bypassing RuntimeActivePlanner.export_nc()'s
    # append_iteration_note() call: that call always writes to the real
    # Waam_tech/experimental/runtime/active/notes.md regardless of context,
    # which would pollute a contributor's live iteration log on every test
    # run. The NC bytes produced are identical either way.
    planner_module, helpers_module = _load_frozen_experimental_planner_module()

    data = json.loads(golden_plan_path.read_text(encoding="utf-8"))
    structured = data["llm_context"]["waam_structured"]
    paths = structured["paths"]
    for layer in structured["layers"]:
        planner_module._rewrite_layer_contour_then_fill(layer, paths)

    temp_plan = tmp_path / "transformed_plan.json"
    temp_plan.write_text(json.dumps(data), encoding="utf-8")

    context = ExperimentalPlannerContext(
        output_dir=tmp_path,
        plan_path=temp_plan,
        features_path=tmp_path / "features.json",
    )
    result_path = helpers_module.default_export_nc(context)
    assert Path(result_path).read_bytes() == golden_experimental_nc_path.read_bytes()
