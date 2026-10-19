"""Toolbar and menu commands for the WAAM workbench."""

from __future__ import annotations

from pathlib import Path

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from .qt_compat import QtWidgets
from .point_cloud_dialog import show_xyz_point_cloud_dialog
from .torch_path_viewer import show_torch_path_viewer
from .ui_wcs import show_wcs_dialog
from .ui_workflow import show_generate_nc_dialog, show_workflow_dialog
from ..core.paths import get_output_dir, get_workspace_root

_ICON_DIR = Path(__file__).resolve().parent.parent / "icons"
_WCS_ICON = str(_ICON_DIR / "wcs.svg")
_FEATURES_ICON = str(_ICON_DIR / "extract_features.svg")
_TORCH_ICON = str(_ICON_DIR / "torch_viewer.svg")
_NC_ICON = str(_ICON_DIR / "nc.svg")
_STEP_ICON = str(_ICON_DIR / "export_step.svg")
_POINT_CLOUD_ICON = str(_ICON_DIR / "point_cloud.svg")


COMMANDS: list[str] = []
MENU: list[tuple[str, str]] = []


def _register_command(name: str, command, *aliases: str) -> None:
    Gui.addCommand(name, command)
    for alias in aliases:
        Gui.addCommand(alias, command)


def _visible_solid_objects(doc) -> list[object]:
    visible_parts = []
    for obj in getattr(doc, "Objects", []):
        try:
            view = obj.ViewObject
            if hasattr(view, "Visibility") and not bool(view.Visibility):
                continue
        except Exception:
            pass
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if shape.isNull():
                continue
        except Exception:
            continue
        visible_parts.append(obj)
    return visible_parts


class CmdWaamSetWcs:
    def GetResources(self):
        return {
            "MenuText": "WAAM: Set WCS",
            "ToolTip": "Set the WAAM work coordinate system, initial parameters, and pipeline options.",
            "Pixmap": _WCS_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        show_wcs_dialog(compact=True)

    def IsActive(self):
        return App.ActiveDocument is not None


_register_command("Waam_SetWCS", CmdWaamSetWcs(), "WaamLite_SetWCS")
COMMANDS.append("Waam_SetWCS")
MENU.append(("WAAM - Set WCS", "Waam_SetWCS"))


class CmdWaamExportStep:
    def GetResources(self):
        return {
            "MenuText": "WAAM: Export STEP",
            "ToolTip": "Export the visible solids to input_step/part.step.",
            "Pixmap": _STEP_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.information(None, "WAAM", "Open a document before exporting a STEP file.")
            return

        visible_parts = _visible_solid_objects(doc)

        if not visible_parts:
            QtWidgets.QMessageBox.information(None, "WAAM", "No visible solids to export.")
            return

        target_dir = get_workspace_root() / "input_step"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(None, "WAAM", f"Could not create STEP output folder:\n{target_dir}\n{exc}")
            return

        target_path = target_dir / "part.step"
        try:
            import ImportGui

            ImportGui.export(visible_parts, str(target_path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(None, "WAAM", f"Failed to export STEP:\n{exc}")
            return

        App.Console.PrintMessage(f"WAAM: Exported visible part to {target_path}\n")
        QtWidgets.QMessageBox.information(None, "WAAM", f"Exported visible part to:\n{target_path}")

    def IsActive(self):
        return App.ActiveDocument is not None


_register_command("Waam_ExportStep", CmdWaamExportStep(), "WaamLite_ExportStep")
COMMANDS.append("Waam_ExportStep")
MENU.append(("WAAM - Export STEP", "Waam_ExportStep"))


class CmdWaamXyzPointCloud:
    def GetResources(self):
        return {
            "MenuText": "WAAM: XYZ Point Cloud",
            "ToolTip": "Export a configurable XYZ point cloud with interior solid-grid samples.",
            "Pixmap": _POINT_CLOUD_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.information(None, "WAAM", "Open a document before creating an XYZ point cloud.")
            return
        show_xyz_point_cloud_dialog()

    def IsActive(self):
        return App.ActiveDocument is not None


_register_command("Waam_XyzPointCloud", CmdWaamXyzPointCloud(), "WaamLite_XyzPointCloud")
COMMANDS.append("Waam_XyzPointCloud")
MENU.append(("WAAM - XYZ Point Cloud", "Waam_XyzPointCloud"))


class CmdWaamPlanner:
    def GetResources(self):
        return {
            "MenuText": "WAAM: Planner",
            "ToolTip": "Create the slice-first WAAM plan from WCS-aligned XY layers.",
            "Pixmap": _FEATURES_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        if App.ActiveDocument is None:
            QtWidgets.QMessageBox.information(None, "WAAM", "Open a document before planning.")
            return
        show_workflow_dialog()

    def IsActive(self):
        return App.ActiveDocument is not None


_register_command("Waam_Planner", CmdWaamPlanner(), "WaamLite_Planner")
COMMANDS.append("Waam_Planner")
MENU.append(("WAAM - Planner", "Waam_Planner"))


class CmdWaamGenerateNc:
    def GetResources(self):
        return {
            "MenuText": "WAAM: NC Creator",
            "ToolTip": "Generate WAAM NC from the current slice plan JSON.",
            "Pixmap": _NC_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        show_generate_nc_dialog()

    def IsActive(self):
        return True


_register_command("Waam_GenerateNc", CmdWaamGenerateNc(), "WaamLite_GenerateNc")
COMMANDS.append("Waam_GenerateNc")
MENU.append(("WAAM - NC Creator", "Waam_GenerateNc"))


class CmdWaamTorchViewer:
    def GetResources(self):
        return {
            "MenuText": "WAAM: Torch Viewer",
            "ToolTip": "Preview WAAM NC or plan JSON toolpaths in the torch viewer.",
            "Pixmap": _TORCH_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        start_dir = str(get_output_dir() / "nc_files")
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None,
            "Select WAAM NC or plan file",
            start_dir,
            "WAAM files (*.nc *.tap *.gcode *.json);;All files (*.*)",
        )
        if not file_path:
            return
        try:
            show_torch_path_viewer(file_path)
        except ImportError as exc:
            QtWidgets.QMessageBox.critical(
                None,
                "WAAM",
                f"Missing dependency for viewer:\n{exc}\nInstall matplotlib and numpy in the FreeCAD environment.",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(None, "WAAM", f"Failed to display torch path:\n{exc}")

    def IsActive(self):
        return True


_register_command("Waam_TorchViewer", CmdWaamTorchViewer(), "WaamLite_TorchViewer")
COMMANDS.append("Waam_TorchViewer")
MENU.append(("WAAM - Torch Viewer", "Waam_TorchViewer"))
