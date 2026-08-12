from __future__ import annotations

import ast
from pathlib import Path


def _auto_scale_set_checked_args(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    args: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "setChecked":
            continue
        target = func.value
        if not (
            isinstance(target, ast.Attribute)
            and target.attr == "auto_scale_cb"
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            continue
        if node.args:
            args.append(ast.unparse(node.args[0]))
    return args


def test_auto_scale_ui_checkboxes_share_default_and_config_loader(repo_root):
    expected = {
        "Waam_tech/ui/ui_wcs.py": [
            "DEFAULT_AUTO_SCALE_ENABLED",
            "auto_scale_enabled_from_config(data)",
        ],
        "Waam_tech/ui/ui_workflow.py": [
            "DEFAULT_AUTO_SCALE_ENABLED",
            "auto_scale_enabled_from_config(cfg)",
        ],
    }

    for rel_path, expected_args in expected.items():
        assert _auto_scale_set_checked_args(repo_root / rel_path) == expected_args


def test_set_wcs_marks_non_nc_parameters_as_traceability_only(repo_root):
    source = (repo_root / "Waam_tech" / "ui" / "ui_wcs.py").read_text(encoding="utf-8")

    for phrase in (
        "recorded for traceability only",
        "not transmitted to the machine",
        "not alter generated paths",
        "Heat input is advisory only",
    ):
        assert phrase in source

    for widget_name in (
        "wire_dia_spin",
        "voltage_spin",
        "current_spin",
        "torch_config_edit",
        "interpass_spin",
    ):
        assert f"self.{widget_name}.setToolTip(_PROCESS_TRACEABILITY_TOOLTIP)" in source

    assert "self.heat_input_disp.setToolTip(_HEAT_INPUT_TOOLTIP)" in source


def test_set_wcs_uses_shared_path_job_detection_without_nested_event_loop(repo_root):
    source = (repo_root / "Waam_tech" / "ui" / "ui_wcs.py").read_text(encoding="utf-8")
    command_source = (repo_root / "Waam_tech" / "ui" / "commands_light.py").read_text(encoding="utf-8")
    resolver_source = (repo_root / "Waam_tech" / "core" / "wcs_resolver.py").read_text(encoding="utf-8")

    assert "from ..core.path_jobs import first_path_job" in source
    assert "job = first_path_job(doc) if doc else None" in source
    assert "QEventLoop" not in source
    assert "loop.exec_" not in source
    assert "def show_wcs_dialog(parent=None, *, compact: bool = False) -> None:" in source
    assert "accepted = show_wcs_dialog" not in command_source
    assert "if not accepted" not in command_source
    assert "def _iter_path_jobs" not in resolver_source


def test_generate_nc_dialog_uses_plan_schema_validator(repo_root):
    source = (repo_root / "Waam_tech" / "ui" / "ui_workflow.py").read_text(encoding="utf-8")

    assert "from ..waamgen.plan_schema import PlanSchemaError, validate_plan_schema" in source
    assert "def _validate_plan_for_nc" in source
    assert "return validate_plan_schema(payload)" in source
    assert "def _has_structured_toolpaths" not in source
    assert "Slice plan validation failed:" in source
