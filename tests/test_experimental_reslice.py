from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from Waam_tech.experimental.base import ExperimentalPlannerContext
from Waam_tech.experimental import reslice


def test_reslice_override_detection_ignores_traceability_only_process_values():
    inert_override = {
        "process": {
            "deposition": {
                "arc_voltage_v": 18.5,
                "arc_current_a": 120.0,
                "wire_diameter_mm": 1.2,
            }
        }
    }

    assert reslice.reslice_override_keys(inert_override) == []
    assert reslice.has_experimental_reslice_override(inert_override) is False


def test_reslice_override_detection_finds_layer_spacing_and_pattern_knobs():
    override = {
        "process": {
            "deposition": {"layer_height_mm": 3.0, "bead_width_mm": 7.0},
            "path": {"deposition_pattern": "hatch_only", "hatch_angle_deg": 90.0},
        }
    }

    assert reslice.reslice_override_keys(override) == [
        "process.deposition.bead_width_mm",
        "process.deposition.layer_height_mm",
        "process.path.deposition_pattern",
        "process.path.hatch_angle_deg",
    ]
    assert reslice.has_experimental_reslice_override(override) is True


def test_reslice_override_detection_finds_enabled_auto_scale():
    override = {"process": {"auto_scale": {"enabled": True}}}

    assert reslice.reslice_override_keys(override) == ["process.auto_scale"]


def test_reslice_override_changes_plan_only_when_values_differ(tmp_path):
    plan_path = tmp_path / "waam_slice_plan.json"
    plan_path.write_text(
        """
{
  "deposition_pattern": "contour_hatch",
  "metadata": {
    "stage_manifest": {
      "auto_scale": {"enabled": false}
    }
  },
  "process": {
    "deposition": {
      "layer_height_mm": 1.5,
      "bead_width_mm": 6.0,
      "overlap_pct": 40.0
    },
    "path": {
      "deposition_pattern": "contour_hatch"
    }
  }
}
""",
        encoding="utf-8",
    )

    same_override = {
        "process": {
            "deposition": {"layer_height_mm": 1.5, "bead_width_mm": 6.0, "overlap_pct": 40.0},
            "path": {"deposition_pattern": "contour_hatch"},
        }
    }
    changed_override = {"process": {"deposition": {"layer_height_mm": 3.0}}}

    assert reslice.reslice_override_changes_plan(same_override, plan_path) is False
    assert reslice.reslice_override_changes_plan(changed_override, plan_path) is True


def test_reslice_override_changes_plan_treats_applied_auto_scale_as_current(tmp_path):
    plan_path = tmp_path / "waam_slice_plan.json"
    plan_path.write_text(
        """
{
  "metadata": {
    "stage_manifest": {
      "auto_scale": {"enabled": true, "applied": true}
    }
  },
  "process": {
    "deposition": {
      "layer_height_mm": 2.1,
      "bead_width_mm": 7.4,
      "overlap_pct": 35.0
    },
    "path": {
      "deposition_pattern": "hatch_only"
    }
  }
}
""",
        encoding="utf-8",
    )
    override = {
        "process": {
            "auto_scale": {"enabled": True},
            "deposition": {"layer_height_mm": 1.5, "bead_width_mm": 6.0, "overlap_pct": 40.0},
            "path": {"deposition_pattern": "hatch_only"},
        }
    }

    assert reslice.reslice_override_changes_plan(override, plan_path) is False


def test_write_scratch_slice_plan_uses_part_workspace_paths_and_config_override(tmp_path, monkeypatch):
    workspace_root = tmp_path / "experimental" / "runtime" / "parts" / "pieza2" / "abc123"
    source_plan = tmp_path / "output" / "waam_slice_plan.json"
    config_path = tmp_path / "standard_waam.json"
    step_path = tmp_path / "part.step"
    context = ExperimentalPlannerContext(
        output_dir=tmp_path / "output",
        plan_path=source_plan,
        features_path=tmp_path / "features.json",
        config_override={"process": {"deposition": {"layer_height_mm": 2.5}}},
        config={"process": {"deposition": {"layer_height_mm": 1.25}}},
        config_path=config_path,
        doc=object(),
        objects=[object()],
        step_path=step_path,
    )
    seen = {}

    def fake_write_slice_plan_for_document(**kwargs):
        seen.update(kwargs)
        Path(kwargs["output_path"]).write_text("{}", encoding="utf-8")
        return SimpleNamespace(
            plan_path=Path(kwargs["output_path"]),
            features_path=Path(kwargs["features_path"]),
            llm_waam_path=Path(kwargs["llm_waam_output_path"]),
            layer_count=4,
            path_count=12,
        )

    monkeypatch.setattr(reslice, "_call_write_slice_plan_for_document", fake_write_slice_plan_for_document)

    artifacts = reslice.write_scratch_slice_plan(
        context,
        workspace_root=workspace_root,
        layer_height_mm=5.0,
        deposition_pattern="hatch_only",
    )

    assert artifacts.plan_path == workspace_root.resolve() / "scratch" / "waam_slice_plan_resliced.json"
    assert seen["features_path"] == workspace_root.resolve() / "scratch" / "waam_slice_plan_resliced_features.json"
    assert seen["llm_waam_output_path"] == workspace_root.resolve() / "scratch" / "waam_slice_plan_resliced.llm_waam"
    assert seen["config_path"] == config_path
    assert seen["step_path"] == step_path
    assert seen["config_override"]["process"]["deposition"]["layer_height_mm"] == 5.0
    assert seen["config_override"]["process"]["path"]["deposition_pattern"] == "hatch_only"
    assert context.plan_path == source_plan
