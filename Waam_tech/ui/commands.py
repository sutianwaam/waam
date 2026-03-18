"""Toolbar and menu commands for the simplified WAAM workbench."""

from __future__ import annotations

from pathlib import Path

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
from .qt_compat import QtWidgets

from ..core.paths import get_output_dir, get_workspace_root
from .torch_path_viewer import show_torch_path_viewer
from .ui_mesh import show_mesh_dialog
from .ui_workflow import (
    show_extract_features_dialog,
    show_generate_nc_dialog,
    show_fuse_ops_mesh_dialog,
    show_openclaw_agent_dialog,
    show_plan_operations_dialog,
    show_plan_operations_dialog,
    show_workflow_dialog,
)

_ICON_DIR = Path(__file__).resolve().parent.parent / "icons"
_WCS_ICON = str(_ICON_DIR / "wcs.svg")
_FEATURES_ICON = str(_ICON_DIR / "extract_features.svg")
_MESH_ICON = str(_ICON_DIR / "mesh.svg")
_VOXEL_ICON = str(_ICON_DIR / "voxel_matrix_new.svg")
_TORCH_ICON = str(_ICON_DIR / "torch_viewer.svg")
_COVERAGE_ICON = str(_ICON_DIR / "torch_coverage_new.svg")
_OPS_ICON = str(_ICON_DIR / "waam_ops.svg")
_LLM_ICON = str(_ICON_DIR / "llm_agent.svg")
_LLM_INPUT_ICON = str(_ICON_DIR / "llm_input.svg")
_NC_ICON = str(_ICON_DIR / "nc.svg")
_FUSE_ICON = str(_ICON_DIR / "merge.svg") if (_ICON_DIR / "merge.svg").is_file() else _OPS_ICON
_STEP_ICON = str(_ICON_DIR / "export_step.svg")


COMMANDS: list[str] = []
MENU: list[tuple[str, str]] = []


class CmdWaamSetWcs:
    def GetResources(self):
        return {
            "MenuText": "WAAM: &Set WCS",
            "ToolTip": "Open dialog to set the work coordinate system.",
            "Accel": "Alt+1",
            "Pixmap": _WCS_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        from .ui_wcs import show_wcs_dialog

        accepted = show_wcs_dialog()
        if not accepted:
            App.Console.PrintMessage("WAAM: WCS dialog cancelled.\n")

    def IsActive(self):
        return App.ActiveDocument is not None


Gui.addCommand("Waam_SetWCS", CmdWaamSetWcs())
COMMANDS.append("Waam_SetWCS")
MENU.append(("WAAM - &Set WCS", "Waam_SetWCS"))


class CmdWaamExportStep:
    def GetResources(self):
        return {
            "MenuText": "WAAM: &Export STEP",
            "ToolTip": "Export the visible part of the active document to input_step/part.step.",
            "Accel": "Alt+2",
            "Pixmap": _STEP_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.information(None, "WAAM", "Open a document before exporting a STEP file.")
            return

        visible_parts = []
        for obj in doc.Objects:
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

        if not visible_parts:
            QtWidgets.QMessageBox.information(None, "WAAM", "No visible solids to export in the active view.")
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

        try:
            App.Console.PrintMessage(f"WAAM: Exported visible part to {target_path}\n")
        except Exception:
            pass
        QtWidgets.QMessageBox.information(None, "WAAM", f"Exported visible part to:\n{target_path}")

    def IsActive(self):
        return App.ActiveDocument is not None


Gui.addCommand("Waam_ExportStep", CmdWaamExportStep())
COMMANDS.append("Waam_ExportStep")
MENU.append(("WAAM - &Export STEP", "Waam_ExportStep"))


class CmdWaamMeshSnapshot:
    def GetResources(self):
        return {
            "MenuText": "WAAM: &Mesh",
            "ToolTip": "Create OBJ snapshots or render existing meshes to PNG.",
            "Accel": "Alt+4",
            "Pixmap": _MESH_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        show_mesh_dialog()

    def IsActive(self):
        return True


Gui.addCommand("Waam_MeshSnapshot", CmdWaamMeshSnapshot())
COMMANDS.append("Waam_MeshSnapshot")
MENU.append(("WAAM - &Mesh", "Waam_MeshSnapshot"))


class CmdWaamVoxelViewer:
    def GetResources(self):
        return {
            "MenuText": "WAAM: &Voxel Matrix",
            "ToolTip": "Visualize a voxel matrix as a 3D point cloud.",
            "Accel": "Alt+5",
            "Pixmap": _VOXEL_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        from .ui_voxel_viewer import show_voxel_viewer_dialog

        show_voxel_viewer_dialog()

    def IsActive(self):
        return App.ActiveDocument is not None


Gui.addCommand("Waam_VoxelViewer", CmdWaamVoxelViewer())
COMMANDS.append("Waam_VoxelViewer")
MENU.append(("WAAM - &Voxel Matrix", "Waam_VoxelViewer"))


class CmdWaamFuseOpsMesh:
    def GetResources(self):
        return {
            "MenuText": "WAAM: LLM &Input Builder",
            "ToolTip": "Prepare input for LLM by fusing operations and mesh context.",
            "Accel": "Alt+6",
            "Pixmap": _LLM_INPUT_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        show_fuse_ops_mesh_dialog()

    def IsActive(self):
        return True


Gui.addCommand("Waam_FuseOpsMesh", CmdWaamFuseOpsMesh())
COMMANDS.append("Waam_FuseOpsMesh")
MENU.append(("WAAM - LLM &Input Builder", "Waam_FuseOpsMesh"))


class CmdWaamGenerateNc:
    def GetResources(self):
        return {
            "MenuText": "WAAM: &NC",
            "ToolTip": "Generate a baseline NC file (waam_baseline.nc) from waam_operations.json.",
            "Accel": "Alt+7",
            "Pixmap": _NC_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        show_generate_nc_dialog()

    def IsActive(self):
        return True


Gui.addCommand("Waam_GenerateNc", CmdWaamGenerateNc())
COMMANDS.append("Waam_GenerateNc")
MENU.append(("WAAM - &NC", "Waam_GenerateNc"))


class CmdWaamTorchCoverage:
    def GetResources(self):
        return {
            "MenuText": "WAAM: Torch &Coverage",
            "ToolTip": "Compare torch toolpath coverage against material voxels.",
            "Accel": "Alt+8",
            "Pixmap": _COVERAGE_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        from .ui_torch_coverage import show_torch_coverage_dialog

        show_torch_coverage_dialog()

    def IsActive(self):
        return App.ActiveDocument is not None



Gui.addCommand("Waam_TorchCoverage", CmdWaamTorchCoverage())
COMMANDS.append("Waam_TorchCoverage")
MENU.append(("WAAM - Torch &Coverage", "Waam_TorchCoverage"))


class CmdWaamQualityImprovement:
    def GetResources(self):
        return {
            "MenuText": "WAAM: &OpenClaw Agent",
            "ToolTip": "Use OpenClaw Agent to iteratively improve NC code with LLM + coverage analysis.",
            "Accel": "Alt+9",
            "Pixmap": _LLM_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        show_openclaw_agent_dialog()

    def IsActive(self):
        return True


Gui.addCommand("Waam_QualityImprovement", CmdWaamQualityImprovement())
COMMANDS.append("Waam_QualityImprovement")
MENU.append(("WAAM - &OpenClaw Agent", "Waam_QualityImprovement"))


class CmdWaamTorchViewer:
    def GetResources(self):
        return {
            "MenuText": "WAAM: Torch Vie&wer",
            "ToolTip": "Preview WAAM NC or plan JSON toolpaths in a 3D matplotlib viewer.",
            "Accel": "Alt+0",
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
                f"Missing dependency for viewer:\n{exc}\nInstall matplotlib (and numpy) in the FreeCAD environment.",
            )
        except Exception as exc:  # pragma: no cover - user feedback only
            QtWidgets.QMessageBox.critical(None, "WAAM", f"Failed to display torch path:\n{exc}")

    def IsActive(self):
        return True


Gui.addCommand("Waam_TorchViewer", CmdWaamTorchViewer())
COMMANDS.append("Waam_TorchViewer")
MENU.append(("WAAM - Torch Vie&wer", "Waam_TorchViewer"))


class CmdWaamWorkflow:
    def GetResources(self):
        return {
            "MenuText": "WAAM: &Planner",
            "ToolTip": "Access feature extraction and planning from a single dialog.",
            "Accel": "Alt+3",
            "Pixmap": _FEATURES_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        if App.ActiveDocument is None:
            QtWidgets.QMessageBox.information(None, "WAAM", "Open a document before running WAAM tools.")
            return
        show_workflow_dialog()

    def IsActive(self):
        return App.ActiveDocument is not None

Gui.addCommand("Waam_Workflow", CmdWaamWorkflow())
COMMANDS.append("Waam_Workflow")
MENU.append(("WAAM - &Planner", "Waam_Workflow"))


class CmdWaamExtractFeaturesLegacy:
    def GetResources(self):
        return {
            "MenuText": "WAAM: E&xtract Features",
            "ToolTip": "Extract WCS-aligned geometry features and store them as JSON.",
            "Pixmap": _FEATURES_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        show_extract_features_dialog()

    def IsActive(self):
        return App.ActiveDocument is not None


class CmdWaamPlanOperationsLegacy:
    def GetResources(self):
        return {
            "MenuText": "WAAM: Plan Operat&ions",
            "ToolTip": "Build deterministic WAAM operations from the extracted features.",
            "Pixmap": _OPS_ICON if _ICON_DIR.is_dir() else "",
        }

    def Activated(self):
        show_plan_operations_dialog()

    def IsActive(self):
        return App.ActiveDocument is not None


Gui.addCommand("Waam_ExtractFeatures", CmdWaamExtractFeaturesLegacy())
Gui.addCommand("Waam_PlanOperations", CmdWaamPlanOperationsLegacy())





# Toolbar order: encourage WCS first, then workflow, mesh, NC, fusion, assistant.
COMMANDS[:] = [
    "Waam_SetWCS",
    "Waam_ExportStep",
    "Waam_Workflow",
    "Waam_MeshSnapshot",
    "Waam_VoxelViewer",
    "Waam_FuseOpsMesh",
    "Waam_GenerateNc",
    "Waam_TorchCoverage",
    "Waam_QualityImprovement",

    "Waam_TorchViewer",
]

MENU[:] = [
    ("WAAM - &Set WCS", "Waam_SetWCS"),
    ("WAAM - &Export STEP", "Waam_ExportStep"),
    ("WAAM - &Planner", "Waam_Workflow"),
    ("WAAM - &Mesh", "Waam_MeshSnapshot"),
    ("WAAM - &Voxel Matrix", "Waam_VoxelViewer"),
    ("WAAM - LLM &Input Builder", "Waam_FuseOpsMesh"),
    ("WAAM - &NC", "Waam_GenerateNc"),
    ("WAAM - Torch &Coverage", "Waam_TorchCoverage"),
    ("WAAM - &OpenClaw Agent", "Waam_QualityImprovement"),

    ("WAAM - Torch Vie&wer", "Waam_TorchViewer"),
]
