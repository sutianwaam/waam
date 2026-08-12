from __future__ import annotations

import copy
import json

import pytest

from Waam_tech.waamgen.plan_schema import PlanSchemaError, validate_plan_schema

MINIMAL_VALID_PLAN = {
    "metadata": {"units": "mm", "wcs": "G54"},
    "llm_context": {
        "waam_structured": {
            "paths": {
                "P0": {"points_xyz": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]},
            },
            "layers": [
                {"i": 0, "z": 0.0, "path_ids": ["P0"]},
                {"i": 1, "z": 1.5, "path_ids": ["P0"]},
            ],
        }
    },
}


def test_valid_plan_passes_with_no_warnings():
    warnings = validate_plan_schema(MINIMAL_VALID_PLAN)
    assert warnings == []


def test_missing_metadata_raises():
    bad = copy.deepcopy(MINIMAL_VALID_PLAN)
    del bad["metadata"]
    with pytest.raises(PlanSchemaError):
        validate_plan_schema(bad)


def test_missing_wcs_raises():
    bad = copy.deepcopy(MINIMAL_VALID_PLAN)
    bad["metadata"]["wcs"] = ""
    with pytest.raises(PlanSchemaError):
        validate_plan_schema(bad)


def test_empty_paths_raises():
    bad = copy.deepcopy(MINIMAL_VALID_PLAN)
    bad["llm_context"]["waam_structured"]["paths"] = {}
    with pytest.raises(PlanSchemaError):
        validate_plan_schema(bad)


def test_empty_layers_raises():
    bad = copy.deepcopy(MINIMAL_VALID_PLAN)
    bad["llm_context"]["waam_structured"]["layers"] = []
    with pytest.raises(PlanSchemaError):
        validate_plan_schema(bad)


def test_layer_missing_path_ids_raises():
    bad = copy.deepcopy(MINIMAL_VALID_PLAN)
    bad["llm_context"]["waam_structured"]["layers"][0]["path_ids"] = []
    with pytest.raises(PlanSchemaError):
        validate_plan_schema(bad)


def test_non_monotonic_z_is_a_warning_not_an_error():
    bad = copy.deepcopy(MINIMAL_VALID_PLAN)
    bad["llm_context"]["waam_structured"]["layers"][0]["z"] = 5.0
    bad["llm_context"]["waam_structured"]["layers"][1]["z"] = 0.0
    warnings = validate_plan_schema(bad)
    assert warnings
    assert "monotonically" in warnings[0]


def test_validate_plan_schema_handles_json_roundtrip():
    text = json.dumps(MINIMAL_VALID_PLAN)
    reloaded = json.loads(text)
    assert validate_plan_schema(reloaded) == []
