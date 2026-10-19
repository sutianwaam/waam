from __future__ import annotations


def test_xyz_point_cloud_command_is_between_step_export_and_planner(repo_root):
    source = (repo_root / "Waam_tech" / "ui" / "commands_light.py").read_text(encoding="utf-8")

    assert (repo_root / "Waam_tech" / "icons" / "point_cloud.svg").is_file()
    assert "_POINT_CLOUD_ICON = str(_ICON_DIR / \"point_cloud.svg\")" in source
    assert "from .point_cloud_dialog import show_xyz_point_cloud_dialog" in source
    assert "\"MenuText\": \"WAAM: XYZ Point Cloud\"" in source
    assert "show_xyz_point_cloud_dialog()" in source
    assert "_register_command(\"Waam_XyzPointCloud\"" in source

    step_idx = source.index("COMMANDS.append(\"Waam_ExportStep\")")
    cloud_idx = source.index("COMMANDS.append(\"Waam_XyzPointCloud\")")
    planner_idx = source.index("COMMANDS.append(\"Waam_Planner\")")
    assert step_idx < cloud_idx < planner_idx


def test_xyz_point_cloud_defaults_to_volume_and_auto_spacing(repo_root):
    source = (repo_root / "Waam_tech" / "ui" / "point_cloud_dialog.py").read_text(encoding="utf-8")

    assert "_DEFAULT_SPACING_FALLBACK_MM = 4.0" in source
    assert "_DEFAULT_SPACING_DIVISIONS = 25.0" in source
    assert "default_spacing = _default_spacing_from_dimensions(initial_dimensions)" in source
    assert "for key in (\"volume\", \"surface\", \"vertices\")" in source
    assert "\"volume\": \"Solid grid (inside + surface)\"" in source
    assert "form.addRow(\"Global size:\", self.dimensions_label)" in source
