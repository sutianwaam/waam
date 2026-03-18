"""Qt dialog for visualizing voxel matrices as point clouds."""

from __future__ import annotations

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from .qt_compat import QtCore, QtWidgets

from ..analysis.voxel_matrix import (
    build_voxel_points_and_rle_payload,
    create_voxel_point_cloud,
    write_voxel_rle_json,
)
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


class VoxelViewerDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WAAM - Voxel Matrix Viewer")
        self.resize(*DEFAULT_DIALOG_SIZE)
        self.setMinimumSize(*DEFAULT_DIALOG_SIZE)

        self.selection_label = QtWidgets.QLabel(self)
        self.selection_label.setWordWrap(True)
        self.refresh_button = QtWidgets.QPushButton("Refresh selection", self)
        self.refresh_button.clicked.connect(self._refresh_selection)

        self.voxel_spin = QtWidgets.QDoubleSpinBox(self)
        self.voxel_spin.setRange(0.1, 25.0)
        self.voxel_spin.setDecimals(3)
        self.voxel_spin.setSingleStep(0.1)
        self.voxel_spin.setValue(2.0)

        self.max_voxels_spin = QtWidgets.QSpinBox(self)
        self.max_voxels_spin.setRange(1000, 50_000_000)
        self.max_voxels_spin.setSingleStep(100_000)
        self.max_voxels_spin.setValue(2_000_000)

        self.point_size_spin = QtWidgets.QSpinBox(self)
        self.point_size_spin.setRange(1, 12)
        self.point_size_spin.setValue(2)

        self.export_rle_check = QtWidgets.QCheckBox("Export RLE JSON for LLM", self)
        self.export_rle_check.setChecked(True)

        self.output_log = QtWidgets.QTextEdit(self)
        self.output_log.setReadOnly(True)
        try:
            self.output_log.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        except Exception:
            self.output_log.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)

        run_button = QtWidgets.QPushButton("Generate &Voxel Point Cloud", self)
        run_button.clicked.connect(self._run)

        selection_group = QtWidgets.QGroupBox("Active part", self)
        selection_layout = QtWidgets.QHBoxLayout(selection_group)
        selection_layout.addWidget(QtWidgets.QLabel("Target:", self))
        selection_layout.addWidget(self.selection_label, 1)
        selection_layout.addWidget(self.refresh_button)

        params_group = QtWidgets.QGroupBox("Voxel settings", self)
        params_layout = QtWidgets.QFormLayout(params_group)
        params_layout.addRow("Voxel size (mm):", self.voxel_spin)
        params_layout.addRow("Max voxels:", self.max_voxels_spin)
        params_layout.addRow("Point size:", self.point_size_spin)
        params_layout.addRow(self.export_rle_check)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(selection_group)
        layout.addWidget(params_group)
        layout.addWidget(self.output_log, 1)
        layout.addWidget(run_button, alignment=QtCore.Qt.AlignRight)

        self._refresh_selection()
    def _refresh_selection(self) -> None:
        obj = _pick_target_object()
        self.selection_label.setText(_format_object_label(obj))

    def _log(self, message: str) -> None:
        self.output_log.append(message)

    def _run(self) -> None:
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.information(self, "WAAM", "Open a document before generating voxels.")
            return
        obj = _pick_target_object()
        if obj is None or not hasattr(obj, "Shape"):
            QtWidgets.QMessageBox.information(self, "WAAM", "Select a solid part before generating voxels.")
            return

        voxel_size = float(self.voxel_spin.value())
        max_voxels = int(self.max_voxels_spin.value())
        point_size = int(self.point_size_spin.value())

        self.output_log.clear()
        self._log("Generating voxel matrix...")

        export_rle = bool(self.export_rle_check.isChecked())

        try:
            if export_rle:
                points, metadata, payload = build_voxel_points_and_rle_payload(
                    doc=doc,
                    obj=obj,
                    voxel_size_mm=voxel_size,
                    max_voxels=max_voxels,
                )
                create_voxel_point_cloud(
                    doc=doc,
                    obj=obj,
                    voxel_size_mm=voxel_size,
                    max_voxels=max_voxels,
                    point_size=point_size,
                    points=points,
                    metadata=metadata,
                )
                out_path = write_voxel_rle_json(payload)
            else:
                _, metadata = create_voxel_point_cloud(
                    doc=doc,
                    obj=obj,
                    voxel_size_mm=voxel_size,
                    max_voxels=max_voxels,
                    point_size=point_size,
                )
                out_path = None
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Voxel visualization failed:\n{exc}")
            return

        dims = metadata.get("dims", [])
        filled = metadata.get("filled_voxels", 0)
        self._log(f"Dims: {dims}")
        self._log(f"Filled voxels: {filled}")
        self._log("Created/updated 'WAAM_VoxelPoints' in the document.")
        if out_path is not None:
            self._log(f"Wrote RLE JSON: {out_path}")


def show_voxel_viewer_dialog() -> bool:
    parent = Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None
    dlg = VoxelViewerDialog(parent=parent)
    return dlg.exec() == QtWidgets.QDialog.Accepted
