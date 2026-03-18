"""Qt dialog for WAAM mesh export/render actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
from .qt_compat import QtCore, QtWidgets

from ..core.paths import get_output_dir, get_workspace_root
from ..mesh_export import convert_obj_to_llm_text, export_object_to_obj, export_objects_to_obj
from ..mesh_export.render_obj import PYRENDER_AVAILABLE, render_obj_to_png
from ..llm_agent.step_to_waam_dsl import (
    default_dsl_output_path,
    write_waam_dsl,
    convert_step_to_waam,
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


def _latest_mesh_export() -> Optional[Path]:
    target_dir = get_output_dir() / "mesh_exports"
    if not target_dir.is_dir():
        return None
    objs = sorted(target_dir.glob("*.obj"), key=lambda path: path.stat().st_mtime, reverse=True)
    return objs[0] if objs else None


def _obj_has_vertices(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.startswith("v "):
                    return True
    except Exception:
        return False
    return False


def _default_step_path() -> Path:
    """Preferred default STEP path (aligns with WAAM toolbar export)."""
    return get_workspace_root() / "input_step" / "part.step"


_DSL_PARAMS_FILENAME = "waam_dsl_params.json"


def _dsl_params_path() -> Path:
    return get_output_dir() / "dsl_waam" / _DSL_PARAMS_FILENAME


def _read_dsl_params() -> dict:
    path = _dsl_params_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_dsl_params(payload: dict) -> None:
    path = _dsl_params_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class MeshDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WAAM - Mesh Tools")
        self.resize(*DEFAULT_DIALOG_SIZE)
        self.setMinimumSize(*DEFAULT_DIALOG_SIZE)
        self._batch_notes: list[str] = []

        self.selection_label = QtWidgets.QLabel(self)
        self.selection_label.setWordWrap(True)
        self.refresh_button = QtWidgets.QPushButton("Refresh visible", self)
        self.refresh_button.clicked.connect(self._refresh_selection)
        self._syncing_params = False

        self.linear_spin = QtWidgets.QDoubleSpinBox(self)
        self.linear_spin.setDecimals(3)
        self.linear_spin.setRange(0.01, 5.0)
        self.linear_spin.setSingleStep(0.05)
        self.linear_spin.setValue(0.25)

        self.angular_spin = QtWidgets.QDoubleSpinBox(self)
        self.angular_spin.setDecimals(3)
        self.angular_spin.setRange(0.05, 3.14)
        self.angular_spin.setSingleStep(0.05)
        self.angular_spin.setValue(0.349)

        export_button = QtWidgets.QPushButton("Export visible view to OBJ", self)
        export_button.clicked.connect(self._do_export)

        run_all_button = QtWidgets.QPushButton("Run &All Mesh Steps", self)
        run_all_button.setToolTip(
            "Run all mesh tools: export OBJ, render PNG, generate LLM mesh text, "
            "generate WAAM DSL, and generate LLM WAAM."
        )
        run_all_button.clicked.connect(self._run_all_mesh_steps)

        selection_group = QtWidgets.QGroupBox("Active view", self)
        selection_layout = QtWidgets.QHBoxLayout(selection_group)
        selection_layout.addWidget(QtWidgets.QLabel("Visible solids:", self))
        selection_layout.addWidget(self.selection_label, 1)
        selection_layout.addWidget(self.refresh_button)

        mesh_group = QtWidgets.QGroupBox("Mesh quality", self)
        mesh_form = QtWidgets.QFormLayout(mesh_group)
        mesh_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        mesh_form.addRow("Linear deflection (mm):", self.linear_spin)
        mesh_form.addRow("Angular deflection (rad):", self.angular_spin)

        export_actions = QtWidgets.QHBoxLayout()
        export_actions.addStretch(1)
        export_actions.addWidget(export_button)
        export_actions.addWidget(run_all_button)

        export_layout = QtWidgets.QVBoxLayout()
        export_layout.addWidget(selection_group)
        export_layout.addWidget(mesh_group)
        export_layout.addStretch(1)
        export_layout.addLayout(export_actions)

        export_tab = QtWidgets.QWidget(self)
        export_tab.setLayout(export_layout)

        self.obj_path_edit = QtWidgets.QLineEdit(self)
        self.obj_path_edit.setPlaceholderText("Path to an OBJ file…")
        obj_browse = QtWidgets.QPushButton("Browse…", self)
        obj_browse.clicked.connect(self._browse_obj)

        self.out_path_edit = QtWidgets.QLineEdit(self)
        self.out_path_edit.setPlaceholderText("part.png")
        out_browse = QtWidgets.QPushButton("Browse…", self)
        out_browse.clicked.connect(self._browse_out)

        self.width_spin = QtWidgets.QSpinBox(self)
        self.width_spin.setRange(64, 4096)
        self.width_spin.setValue(800)
        self.width_spin.setSuffix(" px")
        self.height_spin = QtWidgets.QSpinBox(self)
        self.height_spin.setRange(64, 4096)
        self.height_spin.setValue(600)
        self.height_spin.setSuffix(" px")

        self.background_edit = QtWidgets.QLineEdit("255 255 255 255", self)
        self.background_edit.setToolTip("RGBA values 0-255, e.g. 255 255 255 255")

        render_button = QtWidgets.QPushButton("Render OBJ to PNG", self)
        render_button.clicked.connect(self._do_render)
        if not PYRENDER_AVAILABLE:
            render_button.setEnabled(False)

        in_row = QtWidgets.QHBoxLayout()
        in_row.addWidget(QtWidgets.QLabel("OBJ file:", self))
        in_row.addWidget(self.obj_path_edit, 1)
        in_row.addWidget(obj_browse)

        out_row = QtWidgets.QHBoxLayout()
        out_row.addWidget(QtWidgets.QLabel("PNG output:", self))
        out_row.addWidget(self.out_path_edit, 1)
        out_row.addWidget(out_browse)

        size_row = QtWidgets.QHBoxLayout()
        size_row.addWidget(QtWidgets.QLabel("Width:", self))
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QtWidgets.QLabel("Height:", self))
        size_row.addWidget(self.height_spin)
        size_row.addStretch(1)

        bg_row = QtWidgets.QHBoxLayout()
        bg_row.addWidget(QtWidgets.QLabel("Background RGBA:", self))
        bg_row.addWidget(self.background_edit, 1)

        render_inputs = QtWidgets.QGroupBox("Inputs", self)
        render_inputs_layout = QtWidgets.QVBoxLayout(render_inputs)
        render_inputs_layout.addLayout(in_row)
        render_inputs_layout.addLayout(out_row)

        render_opts = QtWidgets.QGroupBox("Render settings", self)
        render_opts_layout = QtWidgets.QVBoxLayout(render_opts)
        render_opts_layout.addLayout(size_row)
        render_opts_layout.addLayout(bg_row)

        render_layout = QtWidgets.QVBoxLayout()
        render_layout.addWidget(render_inputs)
        render_layout.addWidget(render_opts)
        render_layout.addStretch(1)
        render_layout.addWidget(render_button, alignment=QtCore.Qt.AlignRight)
        if not PYRENDER_AVAILABLE:
            warn = QtWidgets.QLabel(
                "Rendering requires the optional 'pyrender' package. Install pyrender to enable this tab.",
                self,
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #b22222;")
            render_layout.addWidget(warn)

        render_tab = QtWidgets.QWidget(self)
        render_tab.setLayout(render_layout)

        self.llm_obj_edit = QtWidgets.QLineEdit(self)
        self.llm_obj_edit.setPlaceholderText("Path to an OBJ file…")
        llm_obj_browse = QtWidgets.QPushButton("Browse…", self)
        llm_obj_browse.clicked.connect(self._browse_llm_obj)

        self.llm_out_edit = QtWidgets.QLineEdit(self)
        self.llm_out_edit.setPlaceholderText("part.mesh_for_llm")
        llm_out_browse = QtWidgets.QPushButton("Browse…", self)
        llm_out_browse.clicked.connect(self._browse_llm_out)

        self.decimals_spin = QtWidgets.QSpinBox(self)
        self.decimals_spin.setRange(0, 6)
        self.decimals_spin.setValue(2)

        llm_button = QtWidgets.QPushButton("Generate LLM mesh text", self)
        llm_button.clicked.connect(self._generate_llm_mesh)

        llm_in_row = QtWidgets.QHBoxLayout()
        llm_in_row.addWidget(QtWidgets.QLabel("OBJ file:", self))
        llm_in_row.addWidget(self.llm_obj_edit, 1)
        llm_in_row.addWidget(llm_obj_browse)

        llm_out_row = QtWidgets.QHBoxLayout()
        llm_out_row.addWidget(QtWidgets.QLabel("LLM text output:", self))
        llm_out_row.addWidget(self.llm_out_edit, 1)
        llm_out_row.addWidget(llm_out_browse)

        llm_opts_row = QtWidgets.QHBoxLayout()
        llm_opts_row.addWidget(QtWidgets.QLabel("Decimal precision:", self))
        llm_opts_row.addWidget(self.decimals_spin)
        llm_opts_row.addStretch(1)

        llm_inputs = QtWidgets.QGroupBox("Inputs", self)
        llm_inputs_layout = QtWidgets.QVBoxLayout(llm_inputs)
        llm_inputs_layout.addLayout(llm_in_row)
        llm_inputs_layout.addLayout(llm_out_row)

        llm_options = QtWidgets.QGroupBox("Options", self)
        llm_options_layout = QtWidgets.QVBoxLayout(llm_options)
        llm_options_layout.addLayout(llm_opts_row)

        llm_layout = QtWidgets.QVBoxLayout()
        llm_layout.addWidget(llm_inputs)
        llm_layout.addWidget(llm_options)
        llm_layout.addStretch(1)
        llm_layout.addWidget(llm_button, alignment=QtCore.Qt.AlignRight)

        llm_tab = QtWidgets.QWidget(self)
        llm_tab.setLayout(llm_layout)

        # ----------------------------- STEP -> WAAM DSL tab -----------------------------
        self.step_path_edit = QtWidgets.QLineEdit(self)
        self.step_path_edit.setPlaceholderText("Path to a STEP file (.stp/.step)…")
        step_browse = QtWidgets.QPushButton("Browse…", self)
        step_browse.clicked.connect(self._browse_step)

        self.dsl_out_edit = QtWidgets.QLineEdit(self)
        self.dsl_out_edit.setPlaceholderText("part.waam")
        dsl_out_browse = QtWidgets.QPushButton("Browse…", self)
        dsl_out_browse.clicked.connect(self._browse_dsl_out)

        self.layer_height_spin = QtWidgets.QDoubleSpinBox(self)
        self.layer_height_spin.setDecimals(3)
        self.layer_height_spin.setRange(0.01, 50.0)
        self.layer_height_spin.setSingleStep(0.1)
        self.layer_height_spin.setValue(1.2)
        self.layer_height_spin.valueChanged.connect(lambda _=None: self._sync_dsl_params("dsl"))

        self.bead_width_spin = QtWidgets.QDoubleSpinBox(self)
        self.bead_width_spin.setDecimals(3)
        self.bead_width_spin.setRange(0.01, 50.0)
        self.bead_width_spin.setSingleStep(0.5)
        self.bead_width_spin.setValue(6.0)
        self.bead_width_spin.valueChanged.connect(lambda _=None: self._sync_dsl_params("dsl"))

        self.samples_spin = QtWidgets.QSpinBox(self)
        self.samples_spin.setRange(4, 200)
        self.samples_spin.setValue(24)
        self.samples_spin.valueChanged.connect(lambda _=None: self._sync_dsl_params("dsl"))

        self.z_offset_spin = QtWidgets.QDoubleSpinBox(self)
        self.z_offset_spin.setDecimals(3)
        self.z_offset_spin.setRange(-1000.0, 1000.0)
        self.z_offset_spin.setSingleStep(0.1)
        self.z_offset_spin.setValue(-1.2)
        self.z_offset_spin.valueChanged.connect(lambda _=None: self._sync_dsl_params("dsl"))

        self.min_area_spin = QtWidgets.QDoubleSpinBox(self)
        self.min_area_spin.setDecimals(3)
        self.min_area_spin.setRange(0.0, 1e6)
        self.min_area_spin.setSingleStep(1.0)
        self.min_area_spin.setValue(1.0)
        self.min_area_spin.valueChanged.connect(lambda _=None: self._sync_dsl_params("dsl"))

        dsl_button = QtWidgets.QPushButton("Generate WAAM DSL", self)
        dsl_button.clicked.connect(self._generate_dsl)

        step_row = QtWidgets.QHBoxLayout()
        step_row.addWidget(QtWidgets.QLabel("STEP file:", self))
        step_row.addWidget(self.step_path_edit, 1)
        step_row.addWidget(step_browse)

        out_row = QtWidgets.QHBoxLayout()
        out_row.addWidget(QtWidgets.QLabel("DSL output:", self))
        out_row.addWidget(self.dsl_out_edit, 1)
        out_row.addWidget(dsl_out_browse)

        lh_row = QtWidgets.QHBoxLayout()
        lh_row.addWidget(QtWidgets.QLabel("Layer height (Z):", self))
        lh_row.addWidget(self.layer_height_spin)
        lh_row.addStretch(1)

        bw_row = QtWidgets.QHBoxLayout()
        bw_row.addWidget(QtWidgets.QLabel("Bead width:", self))
        bw_row.addWidget(self.bead_width_spin)
        bw_row.addStretch(1)

        samples_row = QtWidgets.QHBoxLayout()
        samples_row.addWidget(QtWidgets.QLabel("Samples per edge:", self))
        samples_row.addWidget(self.samples_spin)
        samples_row.addStretch(1)

        offset_row = QtWidgets.QHBoxLayout()
        offset_row.addWidget(QtWidgets.QLabel("Z offset:", self))
        offset_row.addWidget(self.z_offset_spin)
        offset_row.addStretch(1)

        area_row = QtWidgets.QHBoxLayout()
        area_row.addWidget(QtWidgets.QLabel("Min contour area:", self))
        area_row.addWidget(self.min_area_spin)
        area_row.addStretch(1)

        dsl_inputs = QtWidgets.QGroupBox("Inputs", self)
        dsl_inputs_layout = QtWidgets.QVBoxLayout(dsl_inputs)
        dsl_inputs_layout.addLayout(step_row)
        dsl_inputs_layout.addLayout(out_row)

        dsl_params = QtWidgets.QGroupBox("Parameters", self)
        dsl_params_layout = QtWidgets.QVBoxLayout(dsl_params)
        dsl_params_layout.addLayout(lh_row)
        dsl_params_layout.addLayout(bw_row)
        dsl_params_layout.addLayout(samples_row)
        dsl_params_layout.addLayout(offset_row)
        dsl_params_layout.addLayout(area_row)

        dsl_layout = QtWidgets.QVBoxLayout()
        dsl_layout.addWidget(dsl_inputs)
        dsl_layout.addWidget(dsl_params)
        dsl_layout.addStretch(1)
        dsl_layout.addWidget(dsl_button, alignment=QtCore.Qt.AlignRight)

        dsl_tab = QtWidgets.QWidget(self)
        dsl_tab.setLayout(dsl_layout)

        # ----------------------------- STEP -> LLM WAAM tab -----------------------------
        self.llm_step_path_edit = QtWidgets.QLineEdit(self)
        self.llm_step_path_edit.setPlaceholderText("Path to a STEP file (.stp/.step)…")
        llm_step_browse = QtWidgets.QPushButton("Browse…", self)
        llm_step_browse.clicked.connect(self._browse_llm_step)

        self.llm_dsl_out_edit = QtWidgets.QLineEdit(self)
        self.llm_dsl_out_edit.setPlaceholderText("part.waam")
        llm_dsl_out_browse = QtWidgets.QPushButton("Browse…", self)
        llm_dsl_out_browse.clicked.connect(self._browse_llm_dsl_out)

        self.llm_out_edit2 = QtWidgets.QLineEdit(self)
        self.llm_out_edit2.setPlaceholderText("part.llm_waam")
        llm_out_browse2 = QtWidgets.QPushButton("Browse…", self)
        llm_out_browse2.clicked.connect(self._browse_llm_out2)

        self.llm_layer_height_spin = QtWidgets.QDoubleSpinBox(self)
        self.llm_layer_height_spin.setDecimals(3)
        self.llm_layer_height_spin.setRange(0.01, 50.0)
        self.llm_layer_height_spin.setSingleStep(0.1)
        self.llm_layer_height_spin.setValue(1.2)
        self.llm_layer_height_spin.valueChanged.connect(lambda _=None: self._sync_dsl_params("llm"))

        self.llm_bead_width_spin = QtWidgets.QDoubleSpinBox(self)
        self.llm_bead_width_spin.setDecimals(3)
        self.llm_bead_width_spin.setRange(0.01, 50.0)
        self.llm_bead_width_spin.setSingleStep(0.5)
        self.llm_bead_width_spin.setValue(6.0)
        self.llm_bead_width_spin.valueChanged.connect(lambda _=None: self._sync_dsl_params("llm"))

        self.llm_samples_spin = QtWidgets.QSpinBox(self)
        self.llm_samples_spin.setRange(4, 200)
        self.llm_samples_spin.setValue(24)
        self.llm_samples_spin.valueChanged.connect(lambda _=None: self._sync_dsl_params("llm"))

        self.llm_z_offset_spin = QtWidgets.QDoubleSpinBox(self)
        self.llm_z_offset_spin.setDecimals(3)
        self.llm_z_offset_spin.setRange(-1000.0, 1000.0)
        self.llm_z_offset_spin.setSingleStep(0.1)
        self.llm_z_offset_spin.setValue(-1.2)
        self.llm_z_offset_spin.valueChanged.connect(lambda _=None: self._sync_dsl_params("llm"))

        self.llm_min_area_spin = QtWidgets.QDoubleSpinBox(self)
        self.llm_min_area_spin.setDecimals(3)
        self.llm_min_area_spin.setRange(0.0, 1e6)
        self.llm_min_area_spin.setSingleStep(1.0)
        self.llm_min_area_spin.setValue(1.0)
        self.llm_min_area_spin.valueChanged.connect(lambda _=None: self._sync_dsl_params("llm"))

        self.llm_close_tol_spin = QtWidgets.QDoubleSpinBox(self)
        self.llm_close_tol_spin.setDecimals(3)
        self.llm_close_tol_spin.setRange(0.0, 100.0)
        self.llm_close_tol_spin.setSingleStep(0.1)
        self.llm_close_tol_spin.setValue(0.5)

        llm_dsl_button = QtWidgets.QPushButton("Generate LLM WAAM", self)
        llm_dsl_button.clicked.connect(self._generate_llm_dsl)

        llm_step_row = QtWidgets.QHBoxLayout()
        llm_step_row.addWidget(QtWidgets.QLabel("STEP file:", self))
        llm_step_row.addWidget(self.llm_step_path_edit, 1)
        llm_step_row.addWidget(llm_step_browse)

        llm_out_row1 = QtWidgets.QHBoxLayout()
        llm_out_row1.addWidget(QtWidgets.QLabel("WAAM output:", self))
        llm_out_row1.addWidget(self.llm_dsl_out_edit, 1)
        llm_out_row1.addWidget(llm_dsl_out_browse)

        llm_out_row2 = QtWidgets.QHBoxLayout()
        llm_out_row2.addWidget(QtWidgets.QLabel("LLM WAAM output:", self))
        llm_out_row2.addWidget(self.llm_out_edit2, 1)
        llm_out_row2.addWidget(llm_out_browse2)

        llm_lh_row = QtWidgets.QHBoxLayout()
        llm_lh_row.addWidget(QtWidgets.QLabel("Layer height (Z):", self))
        llm_lh_row.addWidget(self.llm_layer_height_spin)
        llm_lh_row.addStretch(1)

        llm_bw_row = QtWidgets.QHBoxLayout()
        llm_bw_row.addWidget(QtWidgets.QLabel("Bead width:", self))
        llm_bw_row.addWidget(self.llm_bead_width_spin)
        llm_bw_row.addStretch(1)

        llm_samples_row = QtWidgets.QHBoxLayout()
        llm_samples_row.addWidget(QtWidgets.QLabel("Samples per edge:", self))
        llm_samples_row.addWidget(self.llm_samples_spin)
        llm_samples_row.addStretch(1)

        llm_offset_row = QtWidgets.QHBoxLayout()
        llm_offset_row.addWidget(QtWidgets.QLabel("Z offset:", self))
        llm_offset_row.addWidget(self.llm_z_offset_spin)
        llm_offset_row.addStretch(1)

        llm_area_row = QtWidgets.QHBoxLayout()
        llm_area_row.addWidget(QtWidgets.QLabel("Min contour area:", self))
        llm_area_row.addWidget(self.llm_min_area_spin)
        llm_area_row.addStretch(1)

        llm_close_row = QtWidgets.QHBoxLayout()
        llm_close_row.addWidget(QtWidgets.QLabel("Close tolerance:", self))
        llm_close_row.addWidget(self.llm_close_tol_spin)
        llm_close_row.addStretch(1)

        llm_inputs = QtWidgets.QGroupBox("Inputs", self)
        llm_inputs_layout = QtWidgets.QVBoxLayout(llm_inputs)
        llm_inputs_layout.addLayout(llm_step_row)
        llm_inputs_layout.addLayout(llm_out_row1)
        llm_inputs_layout.addLayout(llm_out_row2)

        llm_params = QtWidgets.QGroupBox("Parameters", self)
        llm_params_layout = QtWidgets.QVBoxLayout(llm_params)
        llm_params_layout.addLayout(llm_lh_row)
        llm_params_layout.addLayout(llm_bw_row)
        llm_params_layout.addLayout(llm_samples_row)
        llm_params_layout.addLayout(llm_offset_row)
        llm_params_layout.addLayout(llm_area_row)
        llm_params_layout.addLayout(llm_close_row)

        llm_dsl_layout = QtWidgets.QVBoxLayout()
        llm_dsl_layout.addWidget(llm_inputs)
        llm_dsl_layout.addWidget(llm_params)
        llm_dsl_layout.addStretch(1)
        llm_dsl_layout.addWidget(llm_dsl_button, alignment=QtCore.Qt.AlignRight)

        llm_dsl_tab = QtWidgets.QWidget(self)
        llm_dsl_tab.setLayout(llm_dsl_layout)

        tabs = QtWidgets.QTabWidget(self)
        tabs.setDocumentMode(True)
        tabs.addTab(export_tab, "Create OBJ")
        tabs.addTab(dsl_tab, "STEP → WAAM DSL")
        tabs.addTab(llm_dsl_tab, "STEP → LLM WAAM")
        tabs.addTab(llm_tab, "LLM Mesh")
        tabs.addTab(render_tab, "Render PNG")

        close_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        close_box.rejected.connect(self.reject)

        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addWidget(tabs, 1)
        main_layout.addWidget(close_box)
        self.setLayout(main_layout)

        self._refresh_selection()
        self._populate_latest_paths()
        self._populate_default_step_paths()

    # ------------------------------------------------------------------ helpers
    def _visible_parts(self):
        doc = App.ActiveDocument
        if doc is None:
            return []
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
                pass
            try:
                if shape.Volume == 0 and not shape.BoundBox.isValid():
                    continue
            except Exception:
                pass
            visible_parts.append(obj)
        return visible_parts

    def _record_dsl_params(
        self,
        *,
        source: str,
        step_path: str,
        bead_width: float,
        layer_height: float,
        samples: int,
        z_offset: float,
        min_area: float,
    ) -> None:
        payload = _read_dsl_params()
        entry = {
            "source": source,
            "step_path": step_path,
            "bead_width_mm": float(bead_width),
            "layer_height_mm": float(layer_height),
            "samples_per_edge": int(samples),
            "z_offset": float(z_offset),
            "min_contour_area": float(min_area),
        }
        payload[source] = entry
        payload["last"] = entry
        _write_dsl_params(payload)

    def _sync_dsl_params(self, source: str) -> None:
        if self._syncing_params:
            return
        self._syncing_params = True
        try:
            if source == "dsl":
                self.llm_layer_height_spin.setValue(self.layer_height_spin.value())
                self.llm_bead_width_spin.setValue(self.bead_width_spin.value())
                self.llm_samples_spin.setValue(self.samples_spin.value())
                self.llm_z_offset_spin.setValue(self.z_offset_spin.value())
                self.llm_min_area_spin.setValue(self.min_area_spin.value())
            elif source == "llm":
                self.layer_height_spin.setValue(self.llm_layer_height_spin.value())
                self.bead_width_spin.setValue(self.llm_bead_width_spin.value())
                self.samples_spin.setValue(self.llm_samples_spin.value())
                self.z_offset_spin.setValue(self.llm_z_offset_spin.value())
                self.min_area_spin.setValue(self.llm_min_area_spin.value())
        finally:
            self._syncing_params = False

    def _refresh_selection(self) -> None:
        if App.ActiveDocument is None:
            self.selection_label.setText("No active document.")
            return
        visible_parts = self._visible_parts()
        if not visible_parts:
            self.selection_label.setText("No visible solids in active view.")
            return
        labels = [
            getattr(obj, "Label", None) or getattr(obj, "Name", None) or "Unnamed"
            for obj in visible_parts[:3]
        ]
        extra = f" (+{len(visible_parts) - 3} more)" if len(visible_parts) > 3 else ""
        self.selection_label.setText(f"{len(visible_parts)} visible solid(s): " + ", ".join(labels) + extra)

    def _notify(self, level: str, message: str, *, notify: bool = True) -> None:
        if notify:
            if level == "info":
                QtWidgets.QMessageBox.information(self, "WAAM", message)
            elif level == "warning":
                QtWidgets.QMessageBox.warning(self, "WAAM", message)
            else:
                QtWidgets.QMessageBox.critical(self, "WAAM", message)
            return
        if level == "info":
            return
        prefix = "Warning" if level == "warning" else "Error"
        self._batch_notes.append(f"{prefix}: {message}")

    def _do_export(self, checked: bool = False, *, notify: bool = True) -> Optional[Path]:
        if App.ActiveDocument is None:
            self._notify("warning", "Open a document before exporting a mesh.", notify=notify)
            return None
        visible_parts = self._visible_parts()
        if not visible_parts:
            self._notify("warning", "No visible solids to export in the active view.", notify=notify)
            return None
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            mesh_path = export_objects_to_obj(
                visible_parts,
                mesh_linear_deflection=self.linear_spin.value(),
                mesh_angular_deflection=self.angular_spin.value(),
                filename="part_visible.obj",
            )
        except Exception as exc:  # pragma: no cover - GUI feedback only
            self._notify("error", f"Mesh export failed:\n{exc}", notify=notify)
            return None
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        App.Console.PrintMessage(f"WAAM: mesh snapshot saved to {mesh_path}\n")
        self._notify("info", f"Mesh snapshot saved to:\n{mesh_path}", notify=notify)
        self.obj_path_edit.setText(str(mesh_path))
        default_png = Path(mesh_path).with_name("part.png")
        self.out_path_edit.setText(str(default_png))
        self.llm_obj_edit.setText(str(mesh_path))
        if not self.llm_out_edit.text().strip():
            self.llm_out_edit.setText(str(Path(mesh_path).with_name("part.mesh_for_llm")))
        return mesh_path

    def _run_all_mesh_steps(self, silent: bool = False) -> None:
        notes: list[str] = []
        self._batch_notes = []

        self._do_export(notify=False)

        obj_path = Path(self.obj_path_edit.text().strip())
        if PYRENDER_AVAILABLE:
            if obj_path.is_file():
                self._do_render(notify=False)
            else:
                notes.append("Render skipped (OBJ file not set).")
        else:
            notes.append("Render skipped (pyrender not installed).")

        self._generate_llm_mesh(notify=False)

        if self.step_path_edit.text().strip():
            self._generate_dsl(notify=False)
        else:
            notes.append("WAAM DSL skipped (STEP file not set).")

        if self.llm_step_path_edit.text().strip():
            self._generate_llm_dsl(notify=False)
        else:
            notes.append("LLM WAAM skipped (STEP file not set).")

        all_notes = notes + self._batch_notes
        if all_notes:
            message = "Run all finished with notes:\n" + "\n".join(all_notes)
        else:
            message = "Run all finished."
        if not silent:
            QtWidgets.QMessageBox.information(self, "WAAM", message)
        self._batch_notes = []

    def _browse_obj(self) -> None:
        start_dir = self.obj_path_edit.text().strip() or str(get_output_dir() / "mesh_exports")
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select OBJ file",
            start_dir,
            "OBJ files (*.obj);;All files (*.*)",
        )
        if file_path:
            self.obj_path_edit.setText(file_path)
            guess_png = Path(file_path).with_name("part.png")
            if not self.out_path_edit.text().strip():
                self.out_path_edit.setText(str(guess_png))

    def _browse_out(self) -> None:
        start_path = self.out_path_edit.text().strip() or str(Path.home() / "part.png")
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select PNG output file",
            start_path,
            "PNG files (*.png);;All files (*.*)",
        )
        if file_path:
            self.out_path_edit.setText(file_path)

    def _browse_llm_obj(self) -> None:
        start_dir = (
            self.llm_obj_edit.text().strip()
            or self.obj_path_edit.text().strip()
            or str(get_output_dir() / "mesh_exports")
        )
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select OBJ file",
            start_dir,
            "OBJ files (*.obj);;All files (*.*)",
        )
        if file_path:
            self.llm_obj_edit.setText(file_path)
            if not self.llm_out_edit.text().strip():
                self.llm_out_edit.setText(str(Path(file_path).with_name("part.mesh_for_llm")))

    def _browse_llm_out(self) -> None:
        start_path = self.llm_out_edit.text().strip() or str(Path.home() / "part.mesh_for_llm")
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select LLM mesh output file",
            start_path,
            "Text files (*.txt);;All files (*.*)",
        )
        if file_path:
            self.llm_out_edit.setText(file_path)

    def _parse_background(self):
        text = self.background_edit.text().strip()
        parts = [int(float(x)) for x in text.replace(",", " ").split()]
        if len(parts) not in (3, 4):
            raise ValueError("Background must have 3 or 4 numeric components.")
        if len(parts) == 3:
            parts.append(255)
        return parts

    def _do_render(self, checked: bool = False, *, notify: bool = True) -> Optional[Path]:
        if not PYRENDER_AVAILABLE:
            self._notify(
                "warning",
                "Rendering is disabled because pyrender is not installed.",
                notify=notify,
            )
            return None
        obj_path = Path(self.obj_path_edit.text().strip())
        if not obj_path.is_file():
            self._notify("warning", "Select a valid OBJ file first.", notify=notify)
            return None
        out_path = Path(self.out_path_edit.text().strip() or (obj_path.with_name("part.png")))
        try:
            bg = self._parse_background()
        except ValueError as exc:
            self._notify("warning", str(exc), notify=notify)
            return None

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            png_path = render_obj_to_png(
                obj_path,
                out_path=out_path,
                img_size=(self.width_spin.value(), self.height_spin.value()),
                background=bg,
            )
        except Exception as exc:  # pragma: no cover
            self._notify("error", f"Render failed:\n{exc}", notify=notify)
            return None
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self._notify("info", f"Rendered PNG saved to:\n{png_path}", notify=notify)
        return png_path

    def _generate_llm_mesh(self, checked: bool = False, *, notify: bool = True) -> Optional[Path]:
        obj_text = self.llm_obj_edit.text().strip() or self.obj_path_edit.text().strip()
        if not obj_text:
            self._notify("warning", "Select a source OBJ file first.", notify=notify)
            return None
        obj_path = Path(obj_text)
        if (not obj_path.is_file()) or (not _obj_has_vertices(obj_path)):
            obj = _pick_target_object()
            if obj is None or not hasattr(obj, "Shape"):
                self._notify(
                    "warning",
                    "OBJ file missing or empty. Select a part and export to OBJ first (Mesh tab).",
                    notify=notify,
                )
                return None
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                obj_path = export_object_to_obj(
                    obj,
                    mesh_linear_deflection=self.linear_spin.value(),
                    mesh_angular_deflection=self.angular_spin.value(),
                )
            except Exception as exc:
                QtWidgets.QApplication.restoreOverrideCursor()
                self._notify("error", f"Failed to export OBJ for LLM:\n{exc}", notify=notify)
                return None
            QtWidgets.QApplication.restoreOverrideCursor()
            self.obj_path_edit.setText(str(obj_path))
            self.llm_obj_edit.setText(str(obj_path))

        out_text = self.llm_out_edit.text().strip()

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            target = convert_obj_to_llm_text(
                obj_path,
                out_path=out_text or obj_path.with_name("part.mesh_for_llm"),
                decimals=self.decimals_spin.value(),
            )
        except Exception as exc:
            self._notify("error", f"Failed to generate mesh summary:\n{exc}", notify=notify)
            return None
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.llm_out_edit.setText(str(target))
        self._notify("info", f"LLM mesh text saved to:\n{target}", notify=notify)
        return target

    def _browse_step(self) -> None:
        start_dir = self.step_path_edit.text().strip() or str(_default_step_path().parent)
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select STEP file",
            start_dir,
            "STEP files (*.stp *.step);;All files (*.*)",
        )
        if file_path:
            self.step_path_edit.setText(file_path)
            if not self.dsl_out_edit.text().strip():
                self.dsl_out_edit.setText(str(default_dsl_output_path(file_path)))

    def _browse_dsl_out(self) -> None:
        step_hint = self.step_path_edit.text().strip()
        default_path = (
            default_dsl_output_path(step_hint)
            if step_hint
            else get_output_dir() / "dsl_waam" / "part.waam"
        )
        start_path = self.dsl_out_edit.text().strip() or str(default_path)
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select WAAM DSL output file",
            start_path,
            "WAAM DSL (*.waam *.txt);;All files (*.*)",
        )
        if file_path:
            self.dsl_out_edit.setText(file_path)

    def _browse_llm_step(self) -> None:
        start_dir = self.llm_step_path_edit.text().strip() or str(_default_step_path().parent)
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select STEP file",
            start_dir,
            "STEP files (*.stp *.step);;All files (*.*)",
        )
        if file_path:
            self.llm_step_path_edit.setText(file_path)
            if not self.llm_dsl_out_edit.text().strip():
                self.llm_dsl_out_edit.setText(str(default_dsl_output_path(file_path)))
            if not self.llm_out_edit2.text().strip():
                self.llm_out_edit2.setText(str(default_dsl_output_path(file_path).with_suffix(".llm_waam")))

    def _browse_llm_dsl_out(self) -> None:
        step_hint = self.llm_step_path_edit.text().strip()
        default_path = (
            default_dsl_output_path(step_hint)
            if step_hint
            else get_output_dir() / "dsl_waam" / "part.waam"
        )
        start_path = self.llm_dsl_out_edit.text().strip() or str(default_path)
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select WAAM DSL output file",
            start_path,
            "WAAM DSL (*.waam *.txt);;All files (*.*)",
        )
        if file_path:
            self.llm_dsl_out_edit.setText(file_path)
            if not self.llm_out_edit2.text().strip():
                self.llm_out_edit2.setText(str(Path(file_path).with_suffix(".llm_waam")))

    def _browse_llm_out2(self) -> None:
        step_hint = self.llm_step_path_edit.text().strip()
        guess = (
            default_dsl_output_path(step_hint).with_suffix(".llm_waam")
            if step_hint
            else Path.home() / "part.llm_waam"
        )
        start_path = self.llm_out_edit2.text().strip() or str(guess)
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select LLM WAAM output file",
            start_path,
            "LLM WAAM (*.llm_waam *.txt);;All files (*.*)",
        )
        if file_path:
            self.llm_out_edit2.setText(file_path)

    def _generate_dsl(self, checked: bool = False, *, notify: bool = True) -> Optional[Path]:
        step_path = self.step_path_edit.text().strip()
        if not step_path:
            self._notify("warning", "Select a STEP file first.", notify=notify)
            return None
        step_file = Path(step_path)
        if not step_file.is_file():
            self._notify("warning", f"STEP file not found:\n{step_file}", notify=notify)
            return None

        out_text = self.dsl_out_edit.text().strip() or str(default_dsl_output_path(step_file))
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            target = write_waam_dsl(
                out_text,
                step_path=str(step_file),
                layer_height=self.layer_height_spin.value(),
                bead_width=self.bead_width_spin.value(),
                samples_per_edge=self.samples_spin.value(),
                z_offset=self.z_offset_spin.value(),
                min_contour_area=self.min_area_spin.value(),
                max_layers=None,
                units="mm",
            )
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._notify("error", f"Failed to generate WAAM DSL:\n{exc}", notify=notify)
            return None
        QtWidgets.QApplication.restoreOverrideCursor()

        self.dsl_out_edit.setText(str(target))
        self._record_dsl_params(
            source="dsl",
            step_path=str(step_file),
            bead_width=self.bead_width_spin.value(),
            layer_height=self.layer_height_spin.value(),
            samples=self.samples_spin.value(),
            z_offset=self.z_offset_spin.value(),
            min_area=self.min_area_spin.value(),
        )
        self._notify("info", f"WAAM DSL saved to:\n{target}", notify=notify)
        return target

    def _generate_llm_dsl(self, checked: bool = False, *, notify: bool = True) -> Optional[tuple[Path, Path]]:
        step_path = self.llm_step_path_edit.text().strip()
        if not step_path:
            self._notify("warning", "Select a STEP file first.", notify=notify)
            return None
        step_file = Path(step_path)
        if not step_file.is_file():
            self._notify("warning", f"STEP file not found:\n{step_file}", notify=notify)
            return None

        waam_out = self.llm_dsl_out_edit.text().strip() or str(default_dsl_output_path(step_file))
        llm_out = self.llm_out_edit2.text().strip() or str(Path(waam_out).with_suffix(".llm_waam"))

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            convert_step_to_waam(
                step_path=str(step_file),
                out_path=waam_out,
                layer_height=self.llm_layer_height_spin.value(),
                bead_width=self.llm_bead_width_spin.value(),
                samples_per_edge=self.llm_samples_spin.value(),
                z_offset=self.llm_z_offset_spin.value(),
                min_contour_area=self.llm_min_area_spin.value(),
                max_layers=None,
                stitch_close_tol=self.llm_close_tol_spin.value(),
                emit_llm=True,
                llm_output_path=llm_out,
                units="mm",
            )
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._notify("error", f"Failed to generate LLM WAAM:\n{exc}", notify=notify)
            return None
        QtWidgets.QApplication.restoreOverrideCursor()

        self.llm_dsl_out_edit.setText(str(waam_out))
        self.llm_out_edit2.setText(str(llm_out))
        self._record_dsl_params(
            source="llm",
            step_path=str(step_file),
            bead_width=self.llm_bead_width_spin.value(),
            layer_height=self.llm_layer_height_spin.value(),
            samples=self.llm_samples_spin.value(),
            z_offset=self.llm_z_offset_spin.value(),
            min_area=self.llm_min_area_spin.value(),
        )
        self._notify(
            "info",
            f"LLM WAAM saved to:\n{llm_out}\n(Standard WAAM DSL saved to:\n{waam_out})",
            notify=notify,
        )
        return waam_out, llm_out

    def _populate_latest_paths(self) -> None:
        latest = _latest_mesh_export()
        if latest:
            self.obj_path_edit.setText(str(latest))
            self.out_path_edit.setText(str(latest.with_name("part.png")))
            if not self.llm_obj_edit.text().strip():
                self.llm_obj_edit.setText(str(latest))
            if not self.llm_out_edit.text().strip():
                self.llm_out_edit.setText(str(latest.with_name("part.mesh_for_llm")))

    def _populate_default_step_paths(self) -> None:
        """Prefill STEP inputs with the toolbar export location."""
        default_step = _default_step_path()
        if not self.step_path_edit.text().strip():
            self.step_path_edit.setText(str(default_step))
        if not self.dsl_out_edit.text().strip():
            self.dsl_out_edit.setText(str(default_dsl_output_path(default_step)))

        if not self.llm_step_path_edit.text().strip():
            self.llm_step_path_edit.setText(str(default_step))
        if not self.llm_dsl_out_edit.text().strip():
            self.llm_dsl_out_edit.setText(str(default_dsl_output_path(default_step)))
        if not self.llm_out_edit2.text().strip():
            self.llm_out_edit2.setText(str(default_dsl_output_path(default_step).with_suffix(".llm_waam")))


def show_mesh_dialog():
    parent = Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None
    dlg = MeshDialog(parent=parent)
    dlg.exec_()
    return dlg


__all__ = ["show_mesh_dialog"]
