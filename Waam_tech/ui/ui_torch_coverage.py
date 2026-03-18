"""Qt dialog for comparing torch coverage to material voxels."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence, Tuple

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from .qt_compat import QtCore, QtWidgets

from ..core.paths import get_nc_output_path, get_output_dir
from ..analysis.torch_coverage import build_torch_coverage_payload, write_torch_coverage_json
from .ui_constants import DEFAULT_DIALOG_SIZE


def _pick_target_object():
    doc = App.ActiveDocument
    if doc is None:
        return None
    selection = Gui.Selection.getSelection()
    if selection:
        return selection[0]
    if doc.ActiveObject is not None and hasattr(doc.ActiveObject, "Shape"):
        return doc.ActiveObject
    for obj in doc.Objects:
        if hasattr(obj, "Shape"):
            return obj
    return None


def _format_object_label(obj) -> str:
    if obj is None:
        return "No valid part selected."
    doc_label = getattr(obj.Document, "Label", None) if getattr(obj, "Document", None) else None
    parts = []
    if doc_label:
        parts.append(str(doc_label))
    parts.append(getattr(obj, "Label", None) or getattr(obj, "Name", None) or "Unnamed")
    return " / ".join(parts)


def _default_deposition_params() -> Tuple[float, float]:
    try:
        from ..core.config_loader import load_validated_config

        cfg = load_validated_config()
        return float(cfg.deposition.bead_width_mm), float(cfg.deposition.layer_height_mm)
    except Exception:
        return 2.0, 1.6


def _create_points_feature(
    doc: App.Document,
    name: str,
    label: str,
    points: Sequence[App.Vector],
    *,
    point_size: int = 3,
    point_color: Tuple[float, float, float] = (0.85, 0.15, 0.15),
) -> None:
    try:
        import Points  # type: ignore
    except Exception as exc:  # pragma: no cover - FreeCAD dependency
        raise RuntimeError(f"FreeCAD Points module unavailable: {exc}") from exc

    point_cloud = Points.Points(points)
    existing = doc.getObject(name)
    if existing and getattr(existing, "TypeId", "") == "Points::Feature":
        target = existing
    elif existing:
        raise RuntimeError(f"Object '{name}' exists and is not a Points::Feature.")
    else:
        target = doc.addObject("Points::Feature", name)

    target.Points = point_cloud
    try:
        target.Label = label
    except Exception:
        pass
    try:
        target.ViewObject.PointSize = int(point_size)
    except Exception:
        pass
    try:
        target.ViewObject.PointColor = point_color
    except Exception:
        pass

    doc.recompute()


class TorchCoverageDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WAAM - Torch Coverage")
        self.resize(*DEFAULT_DIALOG_SIZE)
        self.setMinimumSize(*DEFAULT_DIALOG_SIZE)

        self.selection_label = QtWidgets.QLabel(self)
        self.selection_label.setWordWrap(True)
        self.refresh_button = QtWidgets.QPushButton("Refresh selection", self)
        self.refresh_button.clicked.connect(self._refresh_selection)

        self.nc_path_edit = QtWidgets.QLineEdit(self)
        self.nc_browse_button = QtWidgets.QPushButton("Browse...", self)
        self.nc_browse_button.clicked.connect(self._browse_nc_file)
        default_nc = get_nc_output_path()
        self.nc_path_edit.setText(str(default_nc))

        self.voxel_spin = QtWidgets.QDoubleSpinBox(self)
        self.voxel_spin.setRange(0.1, 25.0)
        self.voxel_spin.setDecimals(3)
        self.voxel_spin.setSingleStep(0.1)
        self.voxel_spin.setValue(2.0)

        self.max_voxels_spin = QtWidgets.QSpinBox(self)
        self.max_voxels_spin.setRange(1000, 50_000_000)
        self.max_voxels_spin.setSingleStep(100_000)
        self.max_voxels_spin.setValue(2_000_000)

        default_bead, default_layer = _default_deposition_params()
        self.bead_spin = QtWidgets.QDoubleSpinBox(self)
        self.bead_spin.setRange(0.1, 50.0)
        self.bead_spin.setDecimals(3)
        self.bead_spin.setSingleStep(0.1)
        self.bead_spin.setValue(default_bead)

        self.layer_spin = QtWidgets.QDoubleSpinBox(self)
        self.layer_spin.setRange(0.1, 50.0)
        self.layer_spin.setDecimals(3)
        self.layer_spin.setSingleStep(0.1)
        self.layer_spin.setValue(default_layer)

        self.sample_spin = QtWidgets.QDoubleSpinBox(self)
        self.sample_spin.setRange(0.05, 25.0)
        self.sample_spin.setDecimals(3)
        self.sample_spin.setSingleStep(0.1)
        self.sample_spin.setValue(1.0)

        self.show_missing_check = QtWidgets.QCheckBox("Show missing voxels (material not covered)", self)
        self.show_missing_check.setChecked(True)
        self.show_covered_check = QtWidgets.QCheckBox("Show covered voxels (material + torch)", self)
        self.show_covered_check.setChecked(False)
        self.show_extra_check = QtWidgets.QCheckBox("Show extra voxels (torch outside material)", self)
        self.show_extra_check.setChecked(False)

        self.max_points_spin = QtWidgets.QSpinBox(self)
        self.max_points_spin.setRange(1000, 5_000_000)
        self.max_points_spin.setSingleStep(100_000)
        self.max_points_spin.setValue(200_000)

        self.point_size_spin = QtWidgets.QSpinBox(self)
        self.point_size_spin.setRange(1, 12)
        self.point_size_spin.setValue(3)

        self.export_json_check = QtWidgets.QCheckBox("Write torch coverage JSON (RLE)", self)
        self.export_json_check.setChecked(True)

        self.output_log = QtWidgets.QTextEdit(self)
        self.output_log.setReadOnly(True)
        try:
            self.output_log.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        except Exception:
            self.output_log.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)

        run_button = QtWidgets.QPushButton("Compute &Coverage", self)
        run_button.clicked.connect(self._run)

        selection_group = QtWidgets.QGroupBox("Active part", self)
        selection_layout = QtWidgets.QHBoxLayout(selection_group)
        selection_layout.addWidget(QtWidgets.QLabel("Target:", self))
        selection_layout.addWidget(self.selection_label, 1)
        selection_layout.addWidget(self.refresh_button)

        path_group = QtWidgets.QGroupBox("Torch path", self)
        path_layout = QtWidgets.QHBoxLayout(path_group)
        path_layout.addWidget(QtWidgets.QLabel("NC file:", self))
        path_layout.addWidget(self.nc_path_edit, 1)
        path_layout.addWidget(self.nc_browse_button)

        params_group = QtWidgets.QGroupBox("Coverage settings", self)
        params_layout = QtWidgets.QFormLayout(params_group)
        params_layout.addRow("Voxel size (mm):", self.voxel_spin)
        params_layout.addRow("Max voxels:", self.max_voxels_spin)
        params_layout.addRow("Bead width (mm):", self.bead_spin)
        params_layout.addRow("Layer height (mm):", self.layer_spin)
        params_layout.addRow("Sample step (mm):", self.sample_spin)

        output_group = QtWidgets.QGroupBox("Output options", self)
        output_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        output_layout = QtWidgets.QVBoxLayout(output_group)
        output_layout.setSpacing(6)
        output_layout.addWidget(self.show_missing_check)
        output_layout.addWidget(self.show_covered_check)
        output_layout.addWidget(self.show_extra_check)

        output_form = QtWidgets.QFormLayout()
        output_form.setContentsMargins(0, 0, 0, 0)
        output_form.addRow("Max points per cloud:", self.max_points_spin)
        output_form.addRow("Point size:", self.point_size_spin)
        output_layout.addLayout(output_form)

        output_layout.addWidget(self.export_json_check)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(selection_group)
        layout.addWidget(path_group)
        layout.addWidget(params_group)
        layout.addWidget(output_group)
        layout.addWidget(self.output_log, 1)
        layout.addWidget(run_button, alignment=QtCore.Qt.AlignRight)

        self._refresh_selection()
        self._ensure_layout_fit()

    def _ensure_layout_fit(self) -> None:
        try:
            hint = self.sizeHint()
        except Exception:
            return
        min_width = max(DEFAULT_DIALOG_SIZE[0], int(hint.width()))
        min_height = max(DEFAULT_DIALOG_SIZE[1], int(hint.height()))
        self.setMinimumSize(min_width, min_height)
        self.resize(min_width, min_height)

    def _refresh_selection(self) -> None:
        obj = _pick_target_object()
        self.selection_label.setText(_format_object_label(obj))

    def _browse_nc_file(self) -> None:
        start_dir = str(get_output_dir() / "nc_files")
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select NC file",
            start_dir,
            "NC files (*.nc *.tap *.gcode);;All files (*.*)",
        )
        if file_path:
            self.nc_path_edit.setText(file_path)

    def _log(self, message: str) -> None:
        self.output_log.append(message)

    def _run(self) -> None:
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.information(self, "WAAM", "Open a document before running coverage.")
            return
        obj = _pick_target_object()
        if obj is None or not hasattr(obj, "Shape"):
            QtWidgets.QMessageBox.information(self, "WAAM", "Select a solid part before running coverage.")
            return

        nc_path = Path(self.nc_path_edit.text()).expanduser()
        if not nc_path.is_file():
            QtWidgets.QMessageBox.information(self, "WAAM", f"NC file not found:\n{nc_path}")
            return

        voxel_size = float(self.voxel_spin.value())
        max_voxels = int(self.max_voxels_spin.value())
        bead_width = float(self.bead_spin.value())
        layer_height = float(self.layer_spin.value())
        sample_step = float(self.sample_spin.value())
        show_missing = bool(self.show_missing_check.isChecked())
        show_covered = bool(self.show_covered_check.isChecked())
        show_extra = bool(self.show_extra_check.isChecked())
        max_points = int(self.max_points_spin.value())
        point_size = int(self.point_size_spin.value())
        export_json = bool(self.export_json_check.isChecked())

        self.output_log.clear()
        self._log("Computing torch coverage...")
        self._log("Assuming NC coordinates are already in WCS.")

        try:
            payload, point_sets = build_torch_coverage_payload(
                doc=doc,
                obj=obj,
                nc_path=nc_path,
                voxel_size_mm=voxel_size,
                bead_width_mm=bead_width,
                layer_height_mm=layer_height,
                sample_step_mm=sample_step,
                max_voxels=max_voxels,
                collect_missing_points=show_missing,
                collect_covered_points=show_covered,
                collect_extra_points=show_extra,
                max_missing_points=max_points,
                max_covered_points=max_points,
                max_extra_points=max_points,
            )
            out_path = write_torch_coverage_json(payload) if export_json else None
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Coverage failed:\n{exc}")
            return

        summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}

        dims = metadata.get("dims", [])
        self._log(f"Grid Dims: {dims}")
        
        nc_bounds = summary.get("nc_bounds", [])
        grid_bounds = summary.get("grid_bounds", [])
        
        if len(nc_bounds) == 6:
            self._log(f"NC Bounds (X): {nc_bounds[0]:.2f} to {nc_bounds[1]:.2f}")
            self._log(f"NC Bounds (Y): {nc_bounds[2]:.2f} to {nc_bounds[3]:.2f}")
            self._log(f"NC Bounds (Z): {nc_bounds[4]:.2f} to {nc_bounds[5]:.2f}")
        if len(grid_bounds) == 6:
            self._log(f"Grid Bounds (X): {grid_bounds[0]:.2f} to {grid_bounds[1]:.2f}")
            self._log(f"Grid Bounds (Y): {grid_bounds[2]:.2f} to {grid_bounds[3]:.2f}")
            self._log(f"Grid Bounds (Z): {grid_bounds[4]:.2f} to {grid_bounds[5]:.2f}")

        self._log("-" * 20)
        self._log(f"Material voxels: {summary.get('material_voxels', 0)}")
        self._log(f"Torch voxels: {summary.get('torch_voxels', 0)}")
        self._log(f"Covered voxels: {summary.get('covered_voxels', 0)}")
        self._log(f"Missing voxels: {summary.get('missing_voxels', 0)}")
        self._log(f"Extra voxels: {summary.get('extra_voxels', 0)}")
        self._log(f"Coverage: {float(summary.get('coverage_pct', 0.0)):.2f}%")
        self._log(f"Overbuild: {float(summary.get('overbuild_pct', 0.0)):.2f}%")

        if export_json and out_path is not None:
            self._log(f"Wrote coverage JSON: {out_path}")

        if show_missing:
            missing_total = int(summary.get("missing_voxels", 0))
            missing_points = point_sets.get("missing", [])
            if missing_points:
                try:
                    _create_points_feature(
                        doc,
                        "WAAM_MissingVoxels",
                        "WAAM Missing Voxels",
                        missing_points,
                        point_size=point_size,
                        point_color=(0.85, 0.15, 0.15),
                    )
                    self._log("Created/updated 'WAAM_MissingVoxels' in the document.")
                except Exception as exc:
                    QtWidgets.QMessageBox.critical(
                        self,
                        "WAAM",
                        f"Failed to create missing voxel points:\n{exc}",
                    )
                    return
            else:
                self._log("No missing voxels detected at this resolution.")
            if missing_total > len(missing_points):
                self._log(f"Missing point cloud truncated to {len(missing_points)} of {missing_total} voxels.")

        if show_covered:
            covered_total = int(summary.get("covered_voxels", 0))
            covered_points = point_sets.get("covered", [])
            if covered_points:
                try:
                    _create_points_feature(
                        doc,
                        "WAAM_CoveredVoxels",
                        "WAAM Covered Voxels",
                        covered_points,
                        point_size=point_size,
                        point_color=(0.2, 0.8, 0.2),
                    )
                    self._log("Created/updated 'WAAM_CoveredVoxels' in the document.")
                except Exception as exc:
                    QtWidgets.QMessageBox.critical(
                        self,
                        "WAAM",
                        f"Failed to create covered voxel points:\n{exc}",
                    )
                    return
            else:
                self._log("No covered voxels detected at this resolution.")
            if covered_total > len(covered_points):
                self._log(f"Covered point cloud truncated to {len(covered_points)} of {covered_total} voxels.")

        if show_extra:
            extra_total = int(summary.get("extra_voxels", 0))
            extra_points = point_sets.get("extra", [])
            if extra_points:
                try:
                    _create_points_feature(
                        doc,
                        "WAAM_ExtraVoxels",
                        "WAAM Extra Voxels",
                        extra_points,
                        point_size=point_size,
                        point_color=(0.95, 0.65, 0.1),
                    )
                    self._log("Created/updated 'WAAM_ExtraVoxels' in the document.")
                except Exception as exc:
                    QtWidgets.QMessageBox.critical(
                        self,
                        "WAAM",
                        f"Failed to create extra voxel points:\n{exc}",
                    )
                    return
            else:
                self._log("No extra voxels detected at this resolution.")
            if extra_total > len(extra_points):
                self._log(f"Extra point cloud truncated to {len(extra_points)} of {extra_total} voxels.")


def show_torch_coverage_dialog() -> bool:
    parent = Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None
    dlg = TorchCoverageDialog(parent=parent)
    return dlg.exec() == QtWidgets.QDialog.Accepted
