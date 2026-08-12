from __future__ import annotations

import json

import pytest

from Waam_tech.core.config_loader import (
    ConfigError,
    DEFAULT_AUTO_SCALE_ENABLED,
    auto_scale_enabled_from_config,
    validate_deposition_params,
    validate_post_config,
)

VALID_POST = {
    "torch_on": "M62 P0",
    "torch_off": "M63 P0",
    "gas_on": "M64 P0",
    "gas_off": "M65 P0",
    "wire_feed": "M68 E0 Q{wire_feed}",
    "travel_speed": "F{travel_speed}",
}

VALID_DEPOSITION = {
    "bead_width_mm": 6.0,
    "layer_height_mm": 1.5,
    "travel_speed_mm_min": 320.0,
    "wire_feed_mm_min": 3000.0,
    "dwell_per_layer_s": 1.5,
}


def test_validate_post_config_accepts_valid_block():
    post = validate_post_config(VALID_POST)
    assert post.torch_on == "M62 P0"


def test_validate_post_config_rejects_missing_key():
    bad = dict(VALID_POST)
    del bad["torch_on"]
    with pytest.raises(ConfigError):
        validate_post_config(bad)


def test_validate_deposition_params_accepts_valid_block():
    dep = validate_deposition_params(VALID_DEPOSITION)
    assert dep.bead_width_mm == 6.0


@pytest.mark.parametrize("key", list(VALID_DEPOSITION.keys()))
def test_validate_deposition_params_rejects_non_positive_values(key):
    bad = dict(VALID_DEPOSITION)
    bad[key] = 0.0
    with pytest.raises(ConfigError):
        validate_deposition_params(bad)


def test_validate_deposition_params_rejects_non_numeric_value():
    bad = dict(VALID_DEPOSITION)
    bad["bead_width_mm"] = "not-a-number"
    with pytest.raises(ConfigError):
        validate_deposition_params(bad)


def test_auto_scale_enabled_uses_shared_default_when_missing():
    assert auto_scale_enabled_from_config({}) is DEFAULT_AUTO_SCALE_ENABLED
    assert auto_scale_enabled_from_config({"process": {}}) is DEFAULT_AUTO_SCALE_ENABLED


def test_auto_scale_enabled_honors_explicit_config_value():
    assert auto_scale_enabled_from_config({"process": {"auto_scale": {"enabled": True}}}) is True
    assert auto_scale_enabled_from_config({"process": {"auto_scale": {"enabled": False}}}) is False


def test_shipped_auto_scale_default_matches_shared_ui_default(repo_root):
    config_path = repo_root / "Waam_tech" / "config" / "standard_waam.json"
    with config_path.open("r", encoding="utf-8") as handle:
        cfg = json.load(handle)

    assert auto_scale_enabled_from_config(cfg) is DEFAULT_AUTO_SCALE_ENABLED
