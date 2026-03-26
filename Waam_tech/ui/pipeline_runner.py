"""Shared WAAM pipeline logic to be used by multiple UI dialogs."""

import json
from pathlib import Path
from typing import Optional

import FreeCAD as App  # type: ignore
from .qt_compat import QtWidgets, QtCore

from ..core.config_loader import resolve_standard_config_path
from ..core.paths import (
    get_output_dir,
    get_features_path,
    get_operations_path,
    get_operations_with_mesh_path,
    get_nc_output_path,
)
from ..operations import plan_ops
from ..nc import fuse_operations_with_mesh
from ..waamgen import generate_nc as waamgen_generate
from .ui_mesh import MeshDialog
from ..part_features import extract_features_wcs
from ..experimental.base import ExperimentalPlannerContext
from ..experimental.registry import get_experimental_planner, select_experimental_planner
from .wcs_marker import get_wcs_label, get_stored_wcs


def run_full_pipeline(parent_widget: QtWidgets.QWidget, status_callback=None, config_override=None, experimental_planner: Optional[str] = None) -> None:
    """
    Execute the entire WAAM pipeline: Export STEP -> Extract -> Mesh -> Plan -> Fuse -> NC.
    
    Args:
        parent_widget: The parent widget (dialog) for message boxes.
        status_callback: Optional callable(str) to update status text.
        config_override: Optional dict to override standard_waam.json settings.
    """
    def set_status(msg):
        if status_callback:
            status_callback(msg)
        App.Console.PrintMessage(f"WAAM Pipeline: {msg}\n")

    # 1. Export Visible STEP
    doc = App.ActiveDocument
    if doc is None:
        QtWidgets.QMessageBox.warning(parent_widget, "WAAM", "Open a document before running the workflow.")
        return

    # 0. Check WCS
    if get_stored_wcs(doc) is None:
        QtWidgets.QMessageBox.warning(
            parent_widget, 
            "WAAM Pipeline", 
            "No WAAM WCS found.\n\nPlease define and apply a WCS using the 'Start Point' or 'Custom WCS' tabs before running the pipeline."
        )
        return

    visible_parts = []
    for obj in doc.Objects:
        try:
            view = obj.ViewObject
            if hasattr(view, "Visibility") and bool(view.Visibility):
                shape = getattr(obj, "Shape", None)
                if shape and not shape.isNull():
                        visible_parts.append(obj)
        except Exception:
            pass
    
    if not visible_parts:
        QtWidgets.QMessageBox.warning(parent_widget, "WAAM", "No visible solids to export.")
        return

    step_dir = get_output_dir().parent / "input_step"
    step_dir.mkdir(parents=True, exist_ok=True)
    step_path = step_dir / "part.step"

    try:
        import ImportGui
        ImportGui.export(visible_parts, str(step_path))
        set_status(f"Exported STEP to {step_path}")
    except Exception as exc:
        QtWidgets.QMessageBox.critical(parent_widget, "WAAM", f"Failed to export STEP:\n{exc}")
        return

    QtWidgets.QApplication.processEvents()

    # 2. Extract Features
    # Use logic from ui_workflow._ExtractFeaturesPanel._run_extraction but via direct call to avoid circular import.
    try:
        # Determine WCS label
        wcs_label = get_wcs_label(doc) or "G54"
        features_path = get_features_path(create=True, prefer_existing=False)
        
        extract_features_wcs(
            doc,
            wcs_name=wcs_label,
            out_path=str(features_path),
            force_start_to_wcs_origin=True
        )
        set_status(f"Feature extraction done (WCS: {wcs_label})")
    except Exception as exc:
        QtWidgets.QMessageBox.critical(parent_widget, "WAAM", f"Feature extraction failed:\n{exc}")
        return 

    QtWidgets.QApplication.processEvents()

    # 3. Plan Operations
    # features_path is already set above
    ops_path = get_operations_path(create=True, prefer_existing=False)
    
    config_path = None
    try:
        config_path = resolve_standard_config_path()
    except FileNotFoundError:
         QtWidgets.QMessageBox.warning(parent_widget, "WAAM", "standard_waam.json not found.")
         return

    try:
        with features_path.open("r", encoding="utf-8") as handle:
            features_json = json.load(handle)
        with config_path.open("r", encoding="utf-8") as cfg_handle:
            cfg = json.load(cfg_handle)
        
        operations = plan_ops(features_json, config=cfg)
        with ops_path.open("w", encoding="utf-8") as handle:
            json.dump(operations, handle, indent=2)
        set_status(f"Operations planned to {ops_path.name}")
    except Exception as exc:
        QtWidgets.QMessageBox.critical(parent_widget, "WAAM", f"Planning failed:\n{exc}")
        return
        
    QtWidgets.QApplication.processEvents()

    # 4. Run Mesh Steps
    try:
        mesh_dlg = MeshDialog(parent=parent_widget)
        mesh_dlg.step_path_edit.setText(str(step_path))
        mesh_dlg.llm_step_path_edit.setText(str(step_path))
        
        mesh_dlg._run_all_mesh_steps(silent=True)
        set_status("Mesh and DSL generated")
        
        mesh_file = mesh_dlg.llm_out_edit.text().strip()
        llm_waam_file = mesh_dlg.llm_out_edit2.text().strip()
        
        if not mesh_file or not Path(mesh_file).is_file():
                mesh_file = str(get_output_dir() / "mesh_exports" / "part.mesh_for_llm")
        if not llm_waam_file or not Path(llm_waam_file).is_file():
                llm_waam_file = str(get_output_dir() / "dsl_waam" / "part.llm_waam")

    except Exception as exc:
            QtWidgets.QMessageBox.critical(parent_widget, "WAAM", f"Mesh steps failed:\n{exc}")
            return

    QtWidgets.QApplication.processEvents()

    # 5. Connect/Fuse
    fused_path = get_operations_with_mesh_path(create=True, prefer_existing=False)
    try:
        fuse_operations_with_mesh(
            ops_path=ops_path,
            mesh_path=Path(mesh_file),
            output_path=fused_path,
            config_path=config_path,
            config_override=config_override,
            features_path=features_path,
            llm_waam_path=Path(llm_waam_file),
        )
    except Exception as exc:
            QtWidgets.QMessageBox.critical(parent_widget, "WAAM", f"Fusion failed:\n{exc}")
            return

    if experimental_planner is not None:
        planner = get_experimental_planner(experimental_planner) if experimental_planner else None
        if planner is None:
            planner = select_experimental_planner(
                fused_operations_path=fused_path,
                features_path=features_path,
            )

        if planner is None:
            QtWidgets.QMessageBox.warning(
                parent_widget,
                "WAAM Pipeline",
                f"Pipeline finished, but no experimental planner was available.\n\nFused operations are still available at:\n{fused_path}",
            )
            set_status("Pipeline Complete! (No experimental planner selected)")
            return

        try:
            ctx = ExperimentalPlannerContext(
                output_dir=get_output_dir(),
                fused_operations_path=fused_path,
                features_path=features_path,
                config_override=config_override,
            )
            exp_nc = planner.export_nc(ctx)
            set_status(f"Pipeline Complete! ({planner.label} exported)")
            QtWidgets.QMessageBox.information(
                parent_widget,
                "WAAM Pipeline",
                f"Pipeline finished.\nFused operations saved to:\n{fused_path}\n\nExperimental NC saved to:\n{exp_nc}\n\nPlanner used: {planner.label}\n\n"
                + (
                    f"Scaffold notes were refreshed under:\n{get_output_dir() / getattr(planner, 'scaffold_dirname')}\n\n"
                    if getattr(planner, 'scaffold_dirname', None)
                    else ""
                )
                + "Note: default waam_baseline.nc was not overwritten.",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                parent_widget,
                "WAAM Pipeline",
                f"Pipeline finished, but experimental NC export failed:\n{exc}\n\nFused operations are still available at:\n{fused_path}",
            )
            set_status("Pipeline Complete! (Experimental NC export failed)")
        return

    # 6. Generate baseline NC
    nc_out = get_nc_output_path(create=True, prefer_existing=False)
    try:
        set_status("Generating baseline NC...")
        QtWidgets.QApplication.processEvents()
        waamgen_generate(
            fused_path,
            output_path=nc_out,
            max_iters=1,
            geom_tol=0.8,
            plot=False,
        )
    except Exception as exc:
        QtWidgets.QMessageBox.critical(
            parent_widget,
            "WAAM",
            f"Pipeline finished, but NC generation failed:\n{exc}\n\nFused operations are still available at:\n{fused_path}",
        )
        set_status("Pipeline Complete! (NC generation failed)")
        return

    set_status("Pipeline Complete!")
    QtWidgets.QMessageBox.information(
        parent_widget,
        "WAAM Pipeline",
        f"Pipeline finished.\nFused operations saved to:\n{fused_path}\n\nBaseline NC saved to:\n{nc_out}",
    )
