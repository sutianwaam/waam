"""Dialogs that expose inputs/outputs for the deterministic WAAM pipeline steps."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Callable, List, Mapping, Optional

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
from .qt_compat import QtCore, QtGui, QtWidgets

from ..core.config_loader import resolve_standard_config_path
from ..core.paths import (
    _output_root_for,
    get_features_path,
    get_operations_path,
    get_operations_with_mesh_path,
    get_output_dir,
    get_nc_output_path,
)
from ..nc import (
    FusionError,
    NcGenerationError,
    fuse_operations_with_mesh,
    generate_baseline_nc,
)
from ..waamgen.pipeline import generate_nc as waamgen_generate
from ..operations import plan_ops
from ..part_features import extract_features_wcs
from .wcs_marker import get_stored_wcs, get_wcs_label
from .ui_constants import DEFAULT_DIALOG_SIZE
from .pipeline_runner import run_full_pipeline
from ..llm_agent.llm_pipeline import build_quality_improvement_payload
from ..llm_agent.ollama_client import (
    run_quality_improvement_generation,
    check_ollama_available,
    check_model_available,
    _DEFAULT_NC_MODEL,
    PlanToNcResult
)

_KNOWN_NC_MODELS = ("openclaw-agent", "gpt-oss:latest", "llama3:latest", "qwen3-vl:8b", "deepseek-coder:6.7b")

_WCS_PRESETS = ("G54", "G55", "G56", "G57", "G58", "G59")
_DSL_PARAMS_FILENAME = "waam_dsl_params.json"


def _dsl_params_path() -> Path:
    return get_output_dir() / "dsl_waam" / _DSL_PARAMS_FILENAME


def _load_last_dsl_params() -> Optional[dict]:
    path = _dsl_params_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    last = data.get("last")
    if isinstance(last, dict):
        return last
    for key in ("dsl", "llm"):
        entry = data.get(key)
        if isinstance(entry, dict):
            return entry
    return None

class _PipelineDialog(QtWidgets.QDialog):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(*DEFAULT_DIALOG_SIZE)
        self.setMinimumSize(*DEFAULT_DIALOG_SIZE)
        self.status_label = QtWidgets.QLabel("Ready.", self)
        self.status_label.setWordWrap(True)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)


class _ExtractFeaturesPanel(QtWidgets.QWidget):
    def __init__(
        self,
        *,
        status_callback: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._set_status = status_callback
        doc = App.ActiveDocument
        doc_label = getattr(doc, "Label", None) if doc else None
        cfg, cfg_path = self._load_config()
        self._config_path = cfg_path
        self._wcs_name = cfg.get("wcs", "G54")
        if doc:
            stored_label = get_wcs_label(doc)
            if stored_label:
                self._wcs_name = stored_label
        self._default_output = get_features_path(create=True, prefer_existing=False)

        self.output_edit = QtWidgets.QLineEdit(str(self._default_output), self)
        self.output_edit.setPlaceholderText("features.json")
        browse = QtWidgets.QPushButton("Browse…", self)
        browse.clicked.connect(self._browse_output)

        inputs_group = QtWidgets.QGroupBox("Inputs", self)
        inputs_form = QtWidgets.QFormLayout(inputs_group)
        inputs_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        doc_display = QtWidgets.QLabel(doc_label or "[None]", self)
        doc_display.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        inputs_form.addRow("Active document:", doc_display)

        wcs_row = QtWidgets.QHBoxLayout()
        self.wcs_combo = QtWidgets.QComboBox(self)
        for preset in _WCS_PRESETS:
            self.wcs_combo.addItem(preset)
        self.wcs_combo.addItem("Custom…")
        self.wcs_combo.currentIndexChanged.connect(self._handle_wcs_combo_change)
        wcs_row.addWidget(self.wcs_combo)
        self.wcs_edit = QtWidgets.QLineEdit(self._wcs_name, self)
        self.wcs_edit.setPlaceholderText("G54")
        self.wcs_edit.textChanged.connect(self._handle_wcs_text_changed)
        wcs_row.addWidget(self.wcs_edit, 1)
        wcs_set_button = QtWidgets.QPushButton("Use current WCS", self)
        wcs_set_button.setToolTip("Load the stored WAAM WCS from the active document.")
        wcs_set_button.clicked.connect(self._use_current_wcs)
        wcs_row.addWidget(wcs_set_button)

        wcs_row.addWidget(wcs_set_button)
        inputs_form.addRow("Target WCS:", wcs_row)
        self._sync_wcs_combo()

        cfg_row = QtWidgets.QHBoxLayout()
        self.config_edit = QtWidgets.QLineEdit(str(self._config_path) if self._config_path else "", self)
        self.config_edit.setPlaceholderText("standard_waam.json")
        cfg_browse = QtWidgets.QPushButton("Browse…", self)
        cfg_browse.clicked.connect(self._browse_config)
        cfg_row.addWidget(self.config_edit, 1)
        cfg_row.addWidget(cfg_browse)
        inputs_form.addRow("standard_waam.json:", cfg_row)

        out_row = QtWidgets.QHBoxLayout()
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(browse)
        outputs_group = QtWidgets.QGroupBox("Outputs", self)
        outputs_form = QtWidgets.QFormLayout(outputs_group)
        outputs_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        outputs_form.addRow("features.json output:", out_row)

        self.run_button = QtWidgets.QPushButton("Extract features", self)
        self.run_button.clicked.connect(self._run_extraction)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(inputs_group)
        layout.addWidget(outputs_group)
        layout.addWidget(self.run_button, alignment=QtCore.Qt.AlignRight)

    def _browse_output(self) -> None:
        start = self.output_edit.text().strip() or str(self._default_output)
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select features.json output",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.output_edit.setText(file_path)

    def _browse_config(self) -> None:
        start = self.config_edit.text().strip() or (str(self._config_path) if self._config_path else "")
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select standard_waam.json",
            start or str(Path.home()),
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.config_edit.setText(file_path)
            self._config_path = Path(file_path)

    def _run_extraction(self, silent: bool = False) -> None:
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.warning(self, "WAAM", "Open a document with a solid body first.")
            return
        wcs_name = (self.wcs_edit.text().strip() or "G54").upper()
        self._wcs_name = wcs_name
        if not self.config_edit.text().strip():
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                "Select standard_waam.json before extracting features.",
            )
            return
        cfg_path = Path(self.config_edit.text().strip())
        if not cfg_path.is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"WAAM config not found:\n{cfg_path}")
            return
        self._config_path = cfg_path
        target = Path(self.output_edit.text().strip() or self._default_output)
        target.parent.mkdir(parents=True, exist_ok=True)

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            extract_features_wcs(
                doc,
                wcs_name=self._wcs_name,
                out_path=str(target),
                force_start_to_wcs_origin=True,
            )
        except Exception as exc:  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Feature extraction failed:\n{exc}")
            self._set_status("Extraction failed.")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        App.Console.PrintMessage(f"WAAM: features saved to {target}\n")
        if not silent:
            QtWidgets.QMessageBox.information(self, "WAAM", f"Features saved to:\n{target}")
        self._set_status(f"Features saved to {target}")

    def _use_current_wcs(self) -> None:
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.information(self, "WAAM", "Open a document and set a WAAM WCS first.")
            return
        placement = get_stored_wcs(doc)
        if placement is None:
            QtWidgets.QMessageBox.information(
                self,
                "WAAM",
                "No WAAM WCS found. Run WAAM: Set WCS before extracting features.",
            )
            return
        label = get_wcs_label(doc) or "G54"
        self.wcs_edit.setText(label)
        self._sync_wcs_combo()
        self._set_status(f"Using WCS {label}")

    def _load_config(self):
        try:
            cfg_path = resolve_standard_config_path()
            with cfg_path.open("r", encoding="utf-8") as handle:
                cfg = json.load(handle)
            return cfg, cfg_path
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                "standard_waam.json not found. Using defaults where possible.",
            )
            return {}, None

    def _handle_wcs_combo_change(self, index: int) -> None:
        text = self.wcs_combo.itemText(index)
        if text and text != "Custom…":
            blocker = QtCore.QSignalBlocker(self.wcs_edit)
            self.wcs_edit.setText(text)
            del blocker
            self.wcs_edit.setEnabled(False)
        else:
            self.wcs_edit.setEnabled(True)
            self.wcs_edit.setFocus()

    def _handle_wcs_text_changed(self, text: str) -> None:
        normalized = (text or "").strip().upper()
        if normalized in _WCS_PRESETS:
            idx = _WCS_PRESETS.index(normalized)
            blocker = QtCore.QSignalBlocker(self.wcs_combo)
            self.wcs_combo.setCurrentIndex(idx)
            del blocker
            self.wcs_edit.setEnabled(False)
        else:
            blocker = QtCore.QSignalBlocker(self.wcs_combo)
            self.wcs_combo.setCurrentIndex(len(_WCS_PRESETS))
            del blocker
            self.wcs_edit.setEnabled(True)

    def _sync_wcs_combo(self) -> None:
        self._handle_wcs_text_changed(self.wcs_edit.text())


class _PlanOperationsPanel(QtWidgets.QWidget):
    def __init__(
        self,
        *,
        status_callback: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._set_status = status_callback
        out_dir = get_output_dir()
        self.features_edit = QtWidgets.QLineEdit(str(get_features_path(out_dir, create=True, prefer_existing=False)), self)
        self.ops_edit = QtWidgets.QLineEdit(str(get_operations_path(out_dir, create=True, prefer_existing=False)), self)
        self.features_edit.setPlaceholderText("features.json")
        self.ops_edit.setPlaceholderText("waam_operations.json")
        self._config_path = self._resolve_config_path()

        features_browse = QtWidgets.QPushButton("Browse…", self)
        features_browse.clicked.connect(self._browse_features)
        ops_browse = QtWidgets.QPushButton("Browse…", self)
        ops_browse.clicked.connect(self._browse_ops)

        row_features = QtWidgets.QHBoxLayout()
        row_features.addWidget(self.features_edit, 1)
        row_features.addWidget(features_browse)

        row_ops = QtWidgets.QHBoxLayout()
        row_ops.addWidget(self.ops_edit, 1)
        row_ops.addWidget(ops_browse)

        self.config_label = QtWidgets.QLabel(str(self._config_path) if self._config_path else "[Not found]", self)
        self.config_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.config_label.setToolTip(str(self._config_path) if self._config_path else "standard_waam.json not found.")

        inputs_group = QtWidgets.QGroupBox("Inputs", self)
        inputs_form = QtWidgets.QFormLayout(inputs_group)
        inputs_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        inputs_form.addRow("features.json input:", row_features)
        inputs_form.addRow("standard_waam.json:", self.config_label)

        outputs_group = QtWidgets.QGroupBox("Outputs", self)
        outputs_form = QtWidgets.QFormLayout(outputs_group)
        outputs_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        outputs_form.addRow("waam_operations.json output:", row_ops)

        overrides_group = QtWidgets.QGroupBox("Overrides (optional)", self)
        overrides_layout = QtWidgets.QVBoxLayout(overrides_group)

        self.override_deposition_check = QtWidgets.QCheckBox(
            "Override deposition bead width/layer height", self
        )
        self.override_deposition_check.setToolTip(
            "Use the values below instead of the config. This disables auto_scale for planning."
        )

        self.override_bead_width_spin = QtWidgets.QDoubleSpinBox(self)
        self.override_bead_width_spin.setDecimals(3)
        self.override_bead_width_spin.setRange(0.01, 50.0)
        self.override_bead_width_spin.setSingleStep(0.5)
        self.override_bead_width_spin.setValue(6.0)

        self.override_layer_height_spin = QtWidgets.QDoubleSpinBox(self)
        self.override_layer_height_spin.setDecimals(3)
        self.override_layer_height_spin.setRange(0.01, 50.0)
        self.override_layer_height_spin.setSingleStep(0.1)
        self.override_layer_height_spin.setValue(1.2)

        override_row = QtWidgets.QHBoxLayout()
        override_row.addWidget(QtWidgets.QLabel("Bead width (mm):", self))
        override_row.addWidget(self.override_bead_width_spin)
        override_row.addSpacing(12)
        override_row.addWidget(QtWidgets.QLabel("Layer height (mm):", self))
        override_row.addWidget(self.override_layer_height_spin)
        override_row.addStretch(1)

        self.load_dsl_params_button = QtWidgets.QPushButton("Load from Mesh Tools", self)
        self.load_dsl_params_button.clicked.connect(self._load_dsl_params)

        overrides_layout.addWidget(self.override_deposition_check)
        overrides_layout.addLayout(override_row)
        overrides_layout.addWidget(self.load_dsl_params_button, alignment=QtCore.Qt.AlignRight)

        self.run_button = QtWidgets.QPushButton("Plan operations", self)
        self.run_button.clicked.connect(self._run_planning)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(inputs_group)
        layout.addWidget(outputs_group)
        layout.addWidget(overrides_group)
        layout.addWidget(self.run_button, alignment=QtCore.Qt.AlignRight)

    def _browse_features(self) -> None:
        start = self.features_edit.text().strip() or str(get_output_dir())
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select features.json",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.features_edit.setText(file_path)

    def _browse_ops(self) -> None:
        start = self.ops_edit.text().strip() or str(get_operations_path(create=True, prefer_existing=False))
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select waam_operations.json output",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.ops_edit.setText(file_path)

    def _load_dsl_params(self) -> None:
        params = _load_last_dsl_params()
        if not params:
            QtWidgets.QMessageBox.information(
                self,
                "WAAM",
                f"No mesh tool parameters found.\nExpected: {_dsl_params_path()}",
            )
            return
        bw = params.get("bead_width_mm")
        lh = params.get("layer_height_mm")
        if isinstance(bw, (int, float)):
            self.override_bead_width_spin.setValue(float(bw))
        if isinstance(lh, (int, float)):
            self.override_layer_height_spin.setValue(float(lh))
        self.override_deposition_check.setChecked(True)

    def _run_planning(self, silent: bool = False) -> None:
        features_path = Path(self.features_edit.text().strip())
        if not features_path.is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"features.json not found:\n{features_path}")
            return
        ops_path = Path(self.ops_edit.text().strip())
        if not ops_path.parent.exists():
            ops_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._config_path:
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                "standard_waam.json not found; cannot plan operations.",
            )
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            with features_path.open("r", encoding="utf-8") as handle:
                features_json = json.load(handle)
            with self._config_path.open("r", encoding="utf-8") as cfg_handle:
                cfg = json.load(cfg_handle)
            if self.override_deposition_check.isChecked():
                cfg = dict(cfg)
                proc = dict(cfg.get("process") or {})
                dep = dict(proc.get("deposition") or {})
                dep["bead_width_mm"] = float(self.override_bead_width_spin.value())
                dep["layer_height_mm"] = float(self.override_layer_height_spin.value())
                proc["deposition"] = dep
                auto = dict(proc.get("auto_scale") or {})
                auto["enabled"] = False
                proc["auto_scale"] = auto
                cfg["process"] = proc
            operations = plan_ops(features_json, config=cfg)
            with ops_path.open("w", encoding="utf-8") as handle:
                json.dump(operations, handle, indent=2)
        except Exception as exc:  # pragma: no cover - GUI reporting
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Operation planning failed:\n{exc}")
            self._set_status("Planning failed.")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        App.Console.PrintMessage(f"WAAM: operations saved to {ops_path}\n")
        if not silent:
            QtWidgets.QMessageBox.information(self, "WAAM", f"Operations saved to:\n{ops_path}")
        self._set_status(f"Operations saved to {ops_path}")

    def _resolve_config_path(self) -> Optional[Path]:
        try:
            return resolve_standard_config_path()
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                "standard_waam.json not found. Check your WAAM configuration.",
            )
            return None

    def set_config_path(self, cfg_path: Optional[Path]) -> None:
        self._config_path = cfg_path
        self.config_label.setText(str(cfg_path) if cfg_path else "[Not found]")
        self.config_label.setToolTip(str(cfg_path) if cfg_path else "standard_waam.json not found.")


class ExtractFeaturesDialog(_PipelineDialog):
    def __init__(self, parent=None) -> None:
        super().__init__("WAAM - Extract Features", parent=parent)
        self.resize(*DEFAULT_DIALOG_SIZE)
        panel = _ExtractFeaturesPanel(status_callback=self._set_status, parent=self)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(panel)
        layout.addWidget(self.status_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)


class PlanOperationsDialog(_PipelineDialog):
    def __init__(self, parent=None) -> None:
        super().__init__("WAAM - Plan Operations", parent=parent)
        self.resize(*DEFAULT_DIALOG_SIZE)
        panel = _PlanOperationsPanel(status_callback=self._set_status, parent=self)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(panel)
        layout.addWidget(self.status_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)


class _FuseOpsMeshPanel(QtWidgets.QWidget):
    def __init__(
        self,
        *,
        status_callback: Optional[callable] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._set_status = status_callback or (lambda msg: None)
        out_dir = get_output_dir()
        default_ops = get_operations_path(out_dir, create=True, prefer_existing=True)
        default_mesh = self._latest_mesh(out_dir / "mesh_exports")
        default_features = get_features_path(out_dir, prefer_existing=True, create=True)
        default_llm_waam = out_dir / "dsl_waam" / "part.llm_waam"
        try:
            default_config = resolve_standard_config_path()
        except FileNotFoundError:
            default_config = None

        self.ops_edit = QtWidgets.QLineEdit(str(default_ops), self)
        self.ops_edit.setPlaceholderText("waam_operations.json")
        self.mesh_edit = QtWidgets.QLineEdit(str(default_mesh) if default_mesh else "", self)
        self.mesh_edit.setPlaceholderText("part.mesh_for_llm.txt")
        self.config_edit = QtWidgets.QLineEdit(str(default_config) if default_config else "", self)
        self.config_edit.setPlaceholderText("standard_waam.json")
        self.features_edit = QtWidgets.QLineEdit(str(default_features), self)
        self.features_edit.setPlaceholderText("features.json")
        self.llm_waam_edit = QtWidgets.QLineEdit(str(default_llm_waam), self)
        self.llm_waam_edit.setPlaceholderText("part.llm_waam")
        self.out_edit = QtWidgets.QLineEdit(str(get_operations_with_mesh_path(out_dir, create=True, prefer_existing=False)), self)
        self.out_edit.setPlaceholderText("waam_operations_with_mesh.json")
        self.spiral_infill_check = QtWidgets.QCheckBox("Use spiral infill (STC)", self)
        self.spiral_infill_check.setToolTip("Overrides config process.path.infill_strategy for this fusion run.")

        ops_browse = QtWidgets.QPushButton("Browse…", self)
        ops_browse.clicked.connect(self._browse_ops)
        mesh_browse = QtWidgets.QPushButton("Browse…", self)
        mesh_browse.clicked.connect(self._browse_mesh)
        config_browse = QtWidgets.QPushButton("Browse…", self)
        config_browse.clicked.connect(self._browse_config)
        features_browse = QtWidgets.QPushButton("Browse…", self)
        features_browse.clicked.connect(self._browse_features)
        llm_browse = QtWidgets.QPushButton("Browse…", self)
        llm_browse.clicked.connect(self._browse_llm_waam)
        out_browse = QtWidgets.QPushButton("Browse…", self)
        out_browse.clicked.connect(self._browse_out)

        row_ops = QtWidgets.QHBoxLayout()
        row_ops.addWidget(self.ops_edit, 1)
        row_ops.addWidget(ops_browse)

        row_config = QtWidgets.QHBoxLayout()
        row_config.addWidget(self.config_edit, 1)
        row_config.addWidget(config_browse)

        row_features = QtWidgets.QHBoxLayout()
        row_features.addWidget(self.features_edit, 1)
        row_features.addWidget(features_browse)

        row_mesh = QtWidgets.QHBoxLayout()
        row_mesh.addWidget(self.mesh_edit, 1)
        row_mesh.addWidget(mesh_browse)

        row_llm = QtWidgets.QHBoxLayout()
        row_llm.addWidget(self.llm_waam_edit, 1)
        row_llm.addWidget(llm_browse)

        row_out = QtWidgets.QHBoxLayout()
        row_out.addWidget(self.out_edit, 1)
        row_out.addWidget(out_browse)

        inputs_group = QtWidgets.QGroupBox("Inputs", self)
        inputs_form = QtWidgets.QFormLayout(inputs_group)
        inputs_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        inputs_form.addRow("waam_operations.json:", row_ops)
        inputs_form.addRow("standard_waam.json:", row_config)
        inputs_form.addRow("features.json:", row_features)
        inputs_form.addRow("mesh_for_llm (txt/obj):", row_mesh)
        inputs_form.addRow("part.llm_waam:", row_llm)

        outputs_group = QtWidgets.QGroupBox("Output", self)
        outputs_form = QtWidgets.QFormLayout(outputs_group)
        outputs_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        outputs_form.addRow("LLM JSON output:", row_out)
        outputs_form.addRow(self.spiral_infill_check)

        run_button = QtWidgets.QPushButton("Build LLM &Input", self)
        run_button.clicked.connect(self._run_fusion)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(inputs_group)
        layout.addWidget(outputs_group)
        layout.addWidget(run_button, alignment=QtCore.Qt.AlignRight)
        self.setLayout(layout)

    def _browse_ops(self) -> None:
        start = self.ops_edit.text().strip() or str(get_output_dir())
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select waam_operations.json",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.ops_edit.setText(file_path)

    def _browse_config(self) -> None:
        start = self.config_edit.text().strip() or str(get_output_dir() / "config")
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select standard_waam.json",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.config_edit.setText(file_path)

    def _browse_features(self) -> None:
        start = self.features_edit.text().strip() or str(get_output_dir())
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select features.json",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.features_edit.setText(file_path)

    def _browse_mesh(self) -> None:
        start = self.mesh_edit.text().strip() or str(get_output_dir())
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select mesh_for_llm",
            start,
            "Mesh context (*.mesh_for_llm *.mesh_for_llm.txt *.txt *.obj);;All files (*.*)",
        )
        if file_path:
            self.mesh_edit.setText(file_path)

    def _browse_llm_waam(self) -> None:
        start = self.llm_waam_edit.text().strip() or str(get_output_dir() / "dsl_waam")
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select part.llm_waam",
            start,
            "WAAM DSL (*.llm_waam *.waam *.txt);;All files (*.*)",
        )
        if file_path:
            self.llm_waam_edit.setText(file_path)

    def _browse_out(self) -> None:
        start = self.out_edit.text().strip() or str(get_operations_with_mesh_path(create=True, prefer_existing=False))
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select fused JSON output",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.out_edit.setText(file_path)

    def _run_fusion(self) -> None:
        ops_text = self.ops_edit.text().strip()
        cfg_text = self.config_edit.text().strip()
        feats_text = self.features_edit.text().strip()
        mesh_text = self.mesh_edit.text().strip()
        llm_text = self.llm_waam_edit.text().strip()
        out_path = Path(self.out_edit.text().strip()) if self.out_edit.text().strip() else None

        if not ops_text:
            QtWidgets.QMessageBox.warning(self, "WAAM", "Select waam_operations.json first.")
            return
        ops_path = Path(ops_text)
        if not ops_path.is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"Operations file not found:\n{ops_path}")
            return
        cfg_path = Path(cfg_text) if cfg_text else None
        if not cfg_path or not cfg_path.is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"WAAM config not found:\n{cfg_path or '[blank]'}")
            return
        if not feats_text:
            QtWidgets.QMessageBox.warning(self, "WAAM", "Select features.json first.")
            return
        feats_path = Path(feats_text)
        if not feats_path.is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"Features file not found:\n{feats_path}")
            return
        if not mesh_text:
            QtWidgets.QMessageBox.warning(self, "WAAM", "Select mesh_for_llm first.")
            return
        mesh_path = Path(mesh_text)
        if not mesh_path.is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"Mesh context file not found:\n{mesh_path}")
            return
        if not llm_text:
            QtWidgets.QMessageBox.warning(self, "WAAM", "Select part.llm_waam first.")
            return
        llm_path = Path(llm_text)
        if not llm_path.is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"LLM WAAM DSL not found:\n{llm_path}")
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            config_override = None
            if self.spiral_infill_check.isChecked():
                config_override = {"process": {"path": {"infill_strategy": "spiral_stc"}}}
            fused_path = fuse_operations_with_mesh(
                ops_path=ops_path,
                mesh_path=mesh_path,
                output_path=out_path,
                config_path=cfg_path,
                config_override=config_override,
                features_path=feats_path,
                llm_waam_path=llm_path,
            )
        except FusionError as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Fusion failed:\n{exc}")
            self._set_status("Fusion failed.")
            return
        except Exception as exc:  # pragma: no cover
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Unexpected fusion error:\n{exc}")
            self._set_status("Fusion failed.")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        App.Console.PrintMessage(f"WAAM: Fused operations+mesh saved to {fused_path}\n")
        QtWidgets.QMessageBox.information(self, "WAAM", f"Fused JSON saved to:\n{fused_path}")
        self._set_status(f"Fused JSON saved to {fused_path}")

    def _latest_mesh(self, export_dir: Path):
        if not export_dir.is_dir():
            return None
        patterns = ("*.mesh_for_llm", "*.mesh_for_llm.txt", "*.txt", "*.obj")
        for pattern in patterns:
            candidates = sorted(export_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                return candidates[0]
        return None


class WorkflowDialog(QtWidgets.QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("WAAM - Features and Plan")
        self.resize(*DEFAULT_DIALOG_SIZE)
        self.setMinimumSize(*DEFAULT_DIALOG_SIZE)
        self.status_label = QtWidgets.QLabel("Ready.", self)
        self.status_label.setWordWrap(True)

        tabs = QtWidgets.QTabWidget(self)
        tabs.setDocumentMode(True)
        self.extract_panel = _ExtractFeaturesPanel(status_callback=self._set_status, parent=self)
        self.plan_panel = _PlanOperationsPanel(status_callback=self._set_status, parent=self)
        tabs.addTab(self.extract_panel, "Extract Features")
        tabs.addTab(self.plan_panel, "Plan Operations")

        run_all_button = QtWidgets.QPushButton("Extract + &Plan", self)
        run_all_button.clicked.connect(self._run_extract_and_plan)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(tabs)
        layout.addWidget(run_all_button, alignment=QtCore.Qt.AlignRight)
        layout.addWidget(self.status_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _run_extract_and_plan(self) -> None:
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.warning(self, "WAAM", "Open a document before running the workflow.")
            return

        wcs_name = (self.extract_panel.wcs_edit.text().strip() or "G54").upper()
        cfg_text = self.extract_panel.config_edit.text().strip()
        if not cfg_text:
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                "Select standard_waam.json before running the workflow.",
            )
            return
        cfg_path = Path(cfg_text)
        if not cfg_path.is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"WAAM config not found:\n{cfg_path}")
            return

        features_path = Path(self.extract_panel.output_edit.text().strip() or self.extract_panel._default_output)
        features_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_panel.features_edit.setText(str(features_path))

        ops_text = self.plan_panel.ops_edit.text().strip()
        if not ops_text:
            ops_text = str(get_operations_path(create=True, prefer_existing=False))
            self.plan_panel.ops_edit.setText(ops_text)
        ops_path = Path(ops_text)
        if not ops_path.parent.exists():
            ops_path.parent.mkdir(parents=True, exist_ok=True)

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            extract_features_wcs(
                doc,
                wcs_name=wcs_name,
                out_path=str(features_path),
                force_start_to_wcs_origin=True,
            )
            with cfg_path.open("r", encoding="utf-8") as cfg_handle:
                cfg = json.load(cfg_handle)
            with features_path.open("r", encoding="utf-8") as handle:
                features_json = json.load(handle)
            operations = plan_ops(features_json, config=cfg)
            with ops_path.open("w", encoding="utf-8") as handle:
                json.dump(operations, handle, indent=2)
            doc.recompute()
        except Exception as exc:  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Extract + plan failed:\n{exc}")
            self._set_status("Extract + plan failed.")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.plan_panel.set_config_path(cfg_path)
        App.Console.PrintMessage(f"WAAM: features saved to {features_path}; operations saved to {ops_path}\n")
        QtWidgets.QMessageBox.information(
            self,
            "WAAM",
            f"Features saved to:\n{features_path}\n\nOperations saved to:\n{ops_path}",
        )
        self._set_status(f"Features saved to {features_path}; operations saved to {ops_path}")


class GenerateNcDialog(_PipelineDialog):
    def __init__(self, parent=None) -> None:
        super().__init__("WAAM - Baseline NC", parent=parent)
        self.resize(*DEFAULT_DIALOG_SIZE)
        out_dir = get_output_dir()
        self.ops_edit = QtWidgets.QLineEdit(self._default_ops_path(out_dir), self)
        self.ops_edit.setPlaceholderText("waam_operations.json")
        self.nc_edit = QtWidgets.QLineEdit(str(get_nc_output_path(out_dir, create=True, prefer_existing=False)), self)
        self.nc_edit.setPlaceholderText("waam_baseline.nc")
        self.use_waamgen_box = QtWidgets.QCheckBox("Use waamgen loop (prefers waam_operations_with_mesh.json)", self)
        self.use_waamgen_box.setToolTip("Use waamgen's iterative solver when available.")
        self.use_waamgen_box.setChecked(True)
        self.use_waamgen_box.toggled.connect(self._sync_ops_for_waamgen)
        self.max_iters_spin = QtWidgets.QSpinBox(self)
        self.max_iters_spin.setRange(1, 10)
        self.max_iters_spin.setValue(1)
        self.max_iters_spin.setToolTip("Maximum waamgen iterations before giving up.")
        self.geom_tol_spin = QtWidgets.QDoubleSpinBox(self)
        self.geom_tol_spin.setDecimals(2)
        self.geom_tol_spin.setRange(0.1, 5.0)
        self.geom_tol_spin.setSingleStep(0.1)
        self.geom_tol_spin.setValue(0.8)
        self.geom_tol_spin.setSuffix(" mm")
        self.geom_tol_spin.setToolTip("Geometry tolerance used for validation and retries.")
        self.plot_box = QtWidgets.QCheckBox("Save 2D/3D plots (--plot)", self)
        self.split_layers_box = QtWidgets.QCheckBox("Split per-layer NC", self)
        self.split_features_box = QtWidgets.QCheckBox("Split per-feature NC", self)
        self.manifest_box = QtWidgets.QCheckBox("Write chunk manifest", self)
        self.manifest_box.setChecked(True)
        self.chunk_dir_edit = QtWidgets.QLineEdit(str(self._default_chunk_dir()), self)
        self.chunk_dir_edit.setPlaceholderText("chunks")

        ops_browse = QtWidgets.QPushButton("Browse…", self)
        ops_browse.clicked.connect(self._browse_ops)
        nc_browse = QtWidgets.QPushButton("Browse…", self)
        nc_browse.clicked.connect(self._browse_nc)
        chunk_browse = QtWidgets.QPushButton("Browse…", self)
        chunk_browse.clicked.connect(self._browse_chunks)

        row_ops = QtWidgets.QHBoxLayout()
        row_ops.addWidget(self.ops_edit, 1)
        row_ops.addWidget(ops_browse)

        row_nc = QtWidgets.QHBoxLayout()
        row_nc.addWidget(self.nc_edit, 1)
        row_nc.addWidget(nc_browse)

        row_chunk = QtWidgets.QHBoxLayout()
        row_chunk.addWidget(self.chunk_dir_edit, 1)
        row_chunk.addWidget(chunk_browse)

        paths_group = QtWidgets.QGroupBox("Paths", self)
        paths_form = QtWidgets.QFormLayout(paths_group)
        paths_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        paths_form.addRow("Operations JSON:", row_ops)
        paths_form.addRow("Baseline NC output:", row_nc)
        paths_form.addRow("Chunk output dir:", row_chunk)

        options_group = QtWidgets.QGroupBox("Options", self)
        options_form = QtWidgets.QFormLayout(options_group)
        options_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        options_form.addRow(self.use_waamgen_box)
        options_form.addRow("Max iterations:", self.max_iters_spin)
        options_form.addRow("Geometry tolerance:", self.geom_tol_spin)
        options_form.addRow(self.plot_box)
        options_form.addRow(self.split_layers_box)
        options_form.addRow(self.split_features_box)
        options_form.addRow(self.manifest_box)

        self.support_trim_box = QtWidgets.QCheckBox("Enable support trimming (baseline only)", self)
        self.support_trim_box.setChecked(True)
        self.support_trim_box.setToolTip(
            "Controls support-based path trimming for the baseline NC generator.\n"
            "Has no effect when the waamgen loop is enabled."
        )
        self.support_radius_scale_spin = QtWidgets.QDoubleSpinBox(self)
        self.support_radius_scale_spin.setRange(0.1, 5.0)
        self.support_radius_scale_spin.setSingleStep(0.1)
        self.support_radius_scale_spin.setValue(1.0)
        self.support_radius_scale_spin.setToolTip(
            "Scale factor applied to the support radius derived from bead width and\n"
            "overhang/overlap limits. Values >1.0 make trimming less aggressive."
        )
        self.discontinuity_split_combo = QtWidgets.QComboBox(self)
        self.discontinuity_split_combo.addItem("Auto (voids/multi-region)", "auto")
        self.discontinuity_split_combo.addItem("Always split", "always")
        self.discontinuity_split_combo.addItem("Never split", "never")
        self.discontinuity_split_combo.setCurrentIndex(0)
        self.discontinuity_split_combo.setToolTip(
            "Controls whether baseline NC splits deposition when paths are disjoint.\n"
            "Auto = split only on layers with voids or multiple disconnected regions."
        )
        options_form.addRow(self.support_trim_box)
        options_form.addRow("Support radius scale:", self.support_radius_scale_spin)
        options_form.addRow("Discontinuity split:", self.discontinuity_split_combo)

        run_button = QtWidgets.QPushButton("Generate &NC", self)
        run_button.clicked.connect(self._run_nc)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(paths_group)
        layout.addWidget(options_group)
        layout.addWidget(run_button, alignment=QtCore.Qt.AlignRight)
        layout.addWidget(self.status_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self._sync_ops_for_waamgen()

    def _browse_ops(self) -> None:
        start = self.ops_edit.text().strip() or str(get_output_dir())
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select waam_operations.json",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.ops_edit.setText(file_path)

    def _browse_nc(self) -> None:
        start = self.nc_edit.text().strip() or str(get_nc_output_path(create=True, prefer_existing=False))
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select NC output file",
            start,
            "NC files (*.nc *.tap *.gcode);;All files (*.*)",
        )
        if file_path:
            self.nc_edit.setText(file_path)

    def _latest_mesh(self, export_dir: Path) -> Optional[Path]:
        if not export_dir.is_dir():
            return None
        patterns = ("*.mesh_for_llm", "*.mesh_for_llm.txt", "*.txt", "*.obj")
        for pattern in patterns:
            candidates = sorted(export_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                return candidates[0]
        return None

    def _latest_llm_waam(self, dsl_dir: Path) -> Optional[Path]:
        if not dsl_dir.is_dir():
            return None
        candidates = sorted(dsl_dir.glob("*.llm_waam"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0]
        candidates = sorted(dsl_dir.glob("*.waam"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0]
        return None

    def _auto_fuse_ops_path(self, ops_path: Path) -> tuple[Optional[Path], Optional[str]]:
        base_dir = _output_root_for(ops_path)
        ops_file = get_operations_path(base_dir, prefer_existing=True, create=False)
        if not ops_file.is_file():
            return None, f"waam_operations.json not found:\n{ops_file}"
        try:
            cfg_path = resolve_standard_config_path()
        except FileNotFoundError:
            cfg_path = None
        if not cfg_path or not Path(cfg_path).is_file():
            return None, "standard_waam.json not found. Select a config in Fuse Plan + Mesh."
        feats_path = get_features_path(base_dir, prefer_existing=True, create=False)
        if not feats_path.is_file():
            return None, f"features.json not found:\n{feats_path}"
        mesh_path = self._latest_mesh(base_dir / "mesh_exports")
        if not mesh_path or not mesh_path.is_file():
            return None, "Mesh context not found. Run Mesh Tools -> export mesh_for_llm first."
        llm_path = self._latest_llm_waam(base_dir / "dsl_waam")
        if not llm_path or not llm_path.is_file():
            return None, "LLM WAAM DSL not found. Run Mesh Tools -> generate part.llm_waam first."

        output_path = get_operations_with_mesh_path(base_dir, prefer_existing=False, create=True)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            fused_path = fuse_operations_with_mesh(
                ops_path=ops_file,
                mesh_path=mesh_path,
                output_path=output_path,
                config_path=cfg_path,
                features_path=feats_path,
                llm_waam_path=llm_path,
            )
        except FusionError as exc:
            return None, f"Fusion failed:\n{exc}"
        except Exception as exc:  # pragma: no cover
            return None, f"Unexpected fusion error:\n{exc}"
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        return fused_path, None



    def _browse_chunks(self) -> None:
        start = self.chunk_dir_edit.text().strip() or str(self._default_chunk_dir())
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select chunk output directory",
            start,
        )
        if dir_path:
            self.chunk_dir_edit.setText(dir_path)

    def _default_ops_path(self, out_dir: Path) -> str:
        fused = get_operations_with_mesh_path(out_dir)
        if fused.is_file():
            return str(fused)
        return str(get_operations_path(out_dir, create=True, prefer_existing=False))

    def _is_fused_ops(self, ops_path: Path) -> bool:
        if ops_path.name == "waam_operations_with_mesh.json":
            return True
        try:
            payload = json.loads(ops_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if isinstance(payload, dict) and payload.get("fused_schema_version"):
            return True
        llm_context = payload.get("llm_context") or {}
        return isinstance(llm_context, dict) and "waam_structured" in llm_context

    def _prefer_fused_ops(self, ops_path: Path) -> Path:
        if self._is_fused_ops(ops_path):
            return ops_path
        base_dir = _output_root_for(ops_path)
        fused = get_operations_with_mesh_path(base_dir, prefer_existing=True, create=False)
        if fused.is_file():
            return fused
        return ops_path

    def _sync_ops_for_waamgen(self, *_args) -> None:
        if not self.use_waamgen_box.isChecked():
            # Baseline mode: support trimming controls are active.
            self.support_trim_box.setEnabled(True)
            self.support_radius_scale_spin.setEnabled(True)
            self.discontinuity_split_combo.setEnabled(True)
            return
        current = self.ops_edit.text().strip()
        if not current:
            return
        ops_path = Path(current)
        preferred = self._prefer_fused_ops(ops_path)
        if preferred != ops_path:
            self.ops_edit.setText(str(preferred))
        # waamgen loop has its own trimming based on process config; disable baseline-only controls.
        self.support_trim_box.setEnabled(False)
        self.support_radius_scale_spin.setEnabled(False)
        self.discontinuity_split_combo.setEnabled(False)

    def _default_chunk_dir(self) -> Path:
        target_nc = Path(self.nc_edit.text().strip() or get_nc_output_path(create=True, prefer_existing=False))
        return target_nc.parent / "chunks"

    def _refresh_ops_with_llm_paths(self, ops_path: Path) -> Path:
        try:
            data = json.loads(ops_path.read_text(encoding="utf-8"))
        except Exception:
            return ops_path

        sources = data.get("sources") or (data.get("llm_context") or {}).get("sources") or {}
        ops_src = sources.get("operations_path")
        mesh_src = sources.get("mesh_context_path")
        if not ops_src or not mesh_src:
            return ops_path

        return fuse_operations_with_mesh(
            ops_path=Path(ops_src),
            mesh_path=Path(mesh_src),
            output_path=ops_path,
            config_path=sources.get("config_path"),
            features_path=sources.get("features_path"),
            llm_waam_path=sources.get("llm_waam_path"),
        )

    def _run_nc(self) -> None:
        ops_path = Path(self.ops_edit.text().strip())
        if not ops_path.is_file():
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                f"Operations file not found:\n{ops_path}",
            )
            return
        target_nc = Path(self.nc_edit.text().strip())
        if not target_nc.parent.exists():
            target_nc.parent.mkdir(parents=True, exist_ok=True)
        split_layers = self.split_layers_box.isChecked()
        split_features = self.split_features_box.isChecked()
        use_waamgen = self.use_waamgen_box.isChecked()
        if use_waamgen:
            ops_path = self._prefer_fused_ops(ops_path)
            if ops_path != Path(self.ops_edit.text().strip()):
                self.ops_edit.setText(str(ops_path))
            if not self._is_fused_ops(ops_path):
                fused_path, error = self._auto_fuse_ops_path(ops_path)
                if not fused_path:
                    detail = f"\n\n{error}" if error else ""
                    QtWidgets.QMessageBox.warning(
                        self,
                        "WAAM",
                        "waamgen requires a fused operations JSON (waam_operations_with_mesh.json).\n"
                        "Auto-fuse failed. Run WAAM: Fuse Plan + Mesh first, then retry."
                        f"{detail}",
                    )
                    self._set_status("NC generation cancelled: fused operations missing.")
                    return
                self.ops_edit.setText(str(fused_path))
                ops_path = fused_path
        chunk_dir = self.chunk_dir_edit.text().strip() or None
        if chunk_dir:
            chunk_dir = Path(chunk_dir)
            chunk_dir.mkdir(parents=True, exist_ok=True)
        write_manifest = self.manifest_box.isChecked()

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            if use_waamgen:
                try:
                    ops_path = self._refresh_ops_with_llm_paths(ops_path)
                except FusionError as exc:
                    QtWidgets.QMessageBox.warning(self, "WAAM", f"Failed to refresh fused ops:\n{exc}")
                output_path = waamgen_generate(
                    ops_path,
                    output_path=target_nc,
                    max_iters=int(self.max_iters_spin.value()),
                    geom_tol=float(self.geom_tol_spin.value()),
                    attempt_dir=chunk_dir,
                    plot=self.plot_box.isChecked(),
                )
            else:
                output_path = generate_baseline_nc(
                    ops_path,
                    output_path=target_nc,
                    split_layers=split_layers,
                    split_features=split_features,
                    chunk_dir=chunk_dir,
                    write_manifest=write_manifest,
                    support_trim_enabled=self.support_trim_box.isChecked(),
                    support_radius_scale=float(self.support_radius_scale_spin.value()),
                    discontinuity_split_mode=str(self.discontinuity_split_combo.currentData() or "auto"),
                )
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(self, "WAAM", f"Operations file missing:\n{ops_path}")
            self._set_status("NC generation failed.")
            return
        except NcGenerationError as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Failed to build NC:\n{exc}")
            self._set_status("NC generation failed.")
            return
        except Exception as exc:  # pragma: no cover
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Unexpected NC error:\n{exc}")
            self._set_status("NC generation failed.")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        App.Console.PrintMessage(f"WAAM: NC saved to {output_path}\n")
        QtWidgets.QMessageBox.information(self, "WAAM", f"NC saved to:\n{output_path}")
        self._set_status(f"NC saved to {output_path}")


class FuseOpsMeshDialog(_PipelineDialog):
    def __init__(self, parent=None) -> None:
        super().__init__("WAAM - LLM Input Builder", parent=parent)
        self.resize(*DEFAULT_DIALOG_SIZE)
        panel = _FuseOpsMeshPanel(status_callback=self._set_status, parent=self)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(panel)
        layout.addWidget(self.status_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

def _parent_window():
    return Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None


def show_extract_features_dialog():
    dlg = ExtractFeaturesDialog(parent=_parent_window())
    dlg.exec_()


def show_plan_operations_dialog():
    dlg = PlanOperationsDialog(parent=_parent_window())
    dlg.exec_()


def show_workflow_dialog():
    dlg = WorkflowDialog(parent=_parent_window())
    dlg.exec_()


def show_generate_nc_dialog():
    dlg = GenerateNcDialog(parent=_parent_window())
    dlg.exec_()


def show_fuse_ops_mesh_dialog():
    dlg = FuseOpsMeshDialog(parent=_parent_window())
    dlg.exec_()



from ..llm_agent.openclaw_worker import OpenClawWorker

class OpenClawAgentPanel(QtWidgets.QWidget):
    def __init__(
        self,
        *,
        status_callback: Optional[Callable[[str], None]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._set_status = status_callback or (lambda msg: None)
        out_dir = get_output_dir()
        
        # Default paths
        def_nc = get_nc_output_path(out_dir, create=True, prefer_existing=True)
        def_ops = get_operations_with_mesh_path(out_dir, create=True, prefer_existing=True)
        def_llm = out_dir / "dsl_waam" / "part.llm_waam"
        def_cov = out_dir / "torch_coverage" / "part_torch_coverage.rle.json"
        def_pts = out_dir / "torch_coverage" / "part_torch_coverage_missing_points.json"

        self.baseline_edit = QtWidgets.QLineEdit(str(def_nc), self)
        self.ops_edit = QtWidgets.QLineEdit(str(def_ops), self)
        self.llm_edit = QtWidgets.QLineEdit(str(def_llm), self)
        self.cov_edit = QtWidgets.QLineEdit(str(def_cov), self)
        self.pts_edit = QtWidgets.QLineEdit(str(def_pts), self)
        
        self.worker: Optional[OpenClawWorker] = None
        self.chat_history: List[Mapping[str, str]] = []

        form_group = QtWidgets.QGroupBox("Input Data Package", self)
        form = QtWidgets.QFormLayout(form_group)
        form.addRow("1. Baseline NC:", self._make_row(self.baseline_edit))
        form.addRow("2. Operations:", self._make_row(self.ops_edit))
        form.addRow("3. LLM Geometry:", self._make_row(self.llm_edit))
        form.addRow("4. Coverage:", self._make_row(self.cov_edit))
        form.addRow("5. Missing Pts:", self._make_row(self.pts_edit))
        
        self.instructions_edit = QtWidgets.QPlainTextEdit(self)
        self.instructions_edit.setPlaceholderText("Instructions for the agent...")
        self.instructions_edit.setMaximumHeight(60)
        form.addRow("6. Instructions:", self.instructions_edit)

        # -- Agent Execution Section --
        agent_group = QtWidgets.QGroupBox("OpenClaw Agent", self)
        agent_layout = QtWidgets.QVBoxLayout(agent_group)

        model_row = QtWidgets.QHBoxLayout()
        self.model_combo = QtWidgets.QComboBox(self)
        self.model_combo.setEditable(True)
        for m in _KNOWN_NC_MODELS:
            self.model_combo.addItem(m)
        self.model_combo.setCurrentText(_DEFAULT_NC_MODEL)
        
        self.iters_spin = QtWidgets.QSpinBox(self)
        self.iters_spin.setRange(1, 10)
        self.iters_spin.setValue(3)

        model_row.addWidget(QtWidgets.QLabel("Model:", self))
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(QtWidgets.QLabel("Max Iters:", self))
        model_row.addWidget(self.iters_spin)
        agent_layout.addLayout(model_row)

        chat_group = QtWidgets.QGroupBox("Agent Chat", self)
        chat_layout = QtWidgets.QVBoxLayout(chat_group)

        self.chat_history_te = QtWidgets.QTextEdit(self)
        self.chat_history_te.setReadOnly(True)
        self.chat_history_te.setPlaceholderText("Conversation history used by the next generation call...")
        self.chat_history_te.setMaximumHeight(180)
        chat_layout.addWidget(self.chat_history_te)

        chat_input_row = QtWidgets.QHBoxLayout()
        self.chat_input_edit = QtWidgets.QLineEdit(self)
        self.chat_input_edit.setPlaceholderText("Type a new user message for the agent memory...")
        self.chat_input_edit.returnPressed.connect(self._send_chat_message)
        self.chat_send_btn = QtWidgets.QPushButton("Send", self)
        self.chat_send_btn.clicked.connect(self._send_chat_message)
        chat_input_row.addWidget(self.chat_input_edit, 1)
        chat_input_row.addWidget(self.chat_send_btn)
        chat_layout.addLayout(chat_input_row)
        agent_layout.addWidget(chat_group)

        btn_row = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("&Start Agent", self)
        self.start_btn.setStyleSheet("font-weight: bold; background-color: #e1f5fe;")
        self.start_btn.clicked.connect(self._start_agent)
        
        self.stop_btn = QtWidgets.QPushButton("Stop Agent", self)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_agent)
        
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        agent_layout.addLayout(btn_row)

        # Interaction Log
        self.log_te = QtWidgets.QTextEdit(self)
        self.log_te.setReadOnly(True)
        self.log_te.setPlaceholderText("Agent thoughts and validation results will appear here...")
        agent_layout.addWidget(QtWidgets.QLabel("Agent Interaction Log:"))
        agent_layout.addWidget(self.log_te, stretch=1)

        # Results
        self.result_te = QtWidgets.QPlainTextEdit(self)
        self.result_te.setReadOnly(True)
        self.result_te.setPlaceholderText("Best G-code output...")
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.result_te.setFont(fixed_font)
        self.result_te.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        agent_layout.addWidget(QtWidgets.QLabel("Best G-Code:"))
        agent_layout.addWidget(self.result_te, stretch=2)

        self.save_btn = QtWidgets.QPushButton("Save Improved NC...", self)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_result)
        agent_layout.addWidget(self.save_btn, alignment=QtCore.Qt.AlignRight)

        # Main Layout
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(form_group, stretch=0)
        layout.addWidget(agent_group, stretch=1)

    def _make_row(self, edit):
        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(edit, 1)
        btn = QtWidgets.QPushButton("Browse...", self)
        btn.clicked.connect(lambda _, e=edit: self._browse(e))
        layout.addWidget(btn)
        w = QtWidgets.QWidget()
        w.setLayout(layout)
        return w

    def _browse(self, edit):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select File", edit.text(), "All files (*.*)")
        if path:
            edit.setText(path)

    def _log(self, level: str, msg: str):
        color = "black"
        if level == "error": color = "red"
        elif level == "warning": color = "orange"
        elif level == "success": color = "green"
        elif level == "debug": color = "gray"
        
        html = f"<span style='color:{color}'><b>[{level.upper()}]</b> {msg}</span>"
        self.log_te.append(html)
        bar = self.log_te.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _render_chat_history(self):
        self.chat_history_te.clear()
        for item in self.chat_history:
            role = str(item.get("role", "user")).strip().lower()
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            if role == "assistant":
                label = "Assistant"
                color = "#2e7d32"
            elif role == "system":
                label = "System"
                color = "#6a1b9a"
            else:
                label = "User"
                color = "#1565c0"
            safe_content = html.escape(content).replace("\n", "<br>")
            self.chat_history_te.append(f"<b style='color:{color}'>{label}:</b> {safe_content}")
        bar = self.chat_history_te.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _append_chat_message(self, role: str, content: str) -> bool:
        text = (content or "").strip()
        if not text:
            return False
        normalized_role = role if role in ("system", "user", "assistant") else "user"
        self.chat_history.append({"role": normalized_role, "content": text})
        self._render_chat_history()
        return True

    def _send_chat_message(self):
        if self.worker and self.worker.isRunning():
            return
        if self._append_chat_message("user", self.chat_input_edit.text()):
            self.chat_input_edit.clear()

    def _get_paths(self) -> dict:
        return {
            "baseline_path": Path(self.baseline_edit.text()),
            "ops_path": Path(self.ops_edit.text()),
            "llm_waam_path": Path(self.llm_edit.text()),
            "coverage_path": Path(self.cov_edit.text()),
            "missing_points_path": Path(self.pts_edit.text()),
        }

    def _check_files(self, paths: dict) -> bool:
        for name, p in paths.items():
            if not p.is_file():
                QtWidgets.QMessageBox.warning(self, "File Not Found", f"{name} not found:\n{p}")
                return False
        return True

    def _start_agent(self):
        paths = self._get_paths()
        if not self._check_files(paths):
            return

        initial_text = self.instructions_edit.toPlainText().strip()
        if initial_text and not self.chat_history:
            self._append_chat_message("user", initial_text)
        history = list(self.chat_history)

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.save_btn.setEnabled(False)
        self.chat_input_edit.setEnabled(False)
        self.chat_send_btn.setEnabled(False)
        self.log_te.clear()
        self.result_te.clear()
        
        self.worker = OpenClawWorker(
            model=self.model_combo.currentText().strip(),
            extra_instructions="",
            history=history,
            max_iterations=self.iters_spin.value(),
            geom_tol=0.8,
            **paths
        )
        
        self.worker.logMessage.connect(self._log)
        self.worker.iterationFinished.connect(self._on_iter_finished)
        self.worker.workerFinished.connect(self._on_worker_finished)
        self.worker.start()

    def _stop_agent(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.stop_btn.setEnabled(False)

    def _on_iter_finished(self, iter_idx: int, gcode: str, is_best: bool):
        if is_best:
            self.result_te.setPlainText(gcode)
            self.save_btn.setEnabled(True)

    def _on_worker_finished(self, best_gcode: str, final_msg: str, is_error: bool):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.chat_input_edit.setEnabled(True)
        self.chat_send_btn.setEnabled(True)
        if self.worker is not None:
            self.chat_history = list(self.worker.history)
            self._render_chat_history()
        
        if is_error:
            self._log("error", final_msg)
            self._set_status("Agent stopped with error.")
            QtWidgets.QMessageBox.critical(self, "Agent Error", final_msg)
        else:
            self._log("info", final_msg)
            self._set_status("Agent run complete.")
            if best_gcode:
                self.result_te.setPlainText(best_gcode)
                self.save_btn.setEnabled(True)

    def _save_result(self):
        code = self.result_te.toPlainText()
        if not code:
            return
        
        baseline_path = Path(self.baseline_edit.text())
        reply = QtWidgets.QMessageBox.question(
            self, 
            "Stitch with Baseline?",
            "Do you want to APPEND this repair code to the end of the original Baseline NC file?\n\n"
            "Yes = Creates a full file (Baseline + Repair)\n"
            "No = Saves only the LLM output shown above",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel
        )

        if reply == QtWidgets.QMessageBox.Cancel:
            return

        final_content = code
        if reply == QtWidgets.QMessageBox.Yes:
            if baseline_path.is_file():
                try:
                    original = baseline_path.read_text(encoding="utf-8", errors="replace")
                    final_content = original + "\n\n(--- WAAM REPAIR PATH --)\n" + code
                except Exception as e:
                    QtWidgets.QMessageBox.warning(self, "Read Error", f"Could not read baseline:\n{e}")
                    return
            else:
                QtWidgets.QMessageBox.warning(self, "Missing Baseline", "Baseline file not found. Saving only LLM output.")

        out_dir = get_output_dir() / "nc_files"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Improved NC", str(out_dir / "waam_repaired.nc"), "NC files (*.nc *.gcode);;All files (*.*)"
        )
        if path:
            try:
                Path(path).write_text(final_content, encoding="utf-8")
                self._set_status(f"Saved to: {path}")
                QtWidgets.QMessageBox.information(self, "Saved", f"File saved successfully to:\n{path}")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Could not save file:\n{e}")

class OpenClawAgentDialog(_PipelineDialog):
    def __init__(self, parent=None) -> None:
        super().__init__("WAAM - OpenClaw Agent", parent=parent)
        self.resize(800, 700)
        panel = OpenClawAgentPanel(status_callback=self._set_status, parent=self)
        
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(panel)
        layout.addWidget(self.status_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

def show_openclaw_agent_dialog():
    dlg = OpenClawAgentDialog(parent=_parent_window())
    dlg.exec_()


def show_quality_improvement_dialog():
    # Backward-compatible wrapper for existing imports/callers.
    show_openclaw_agent_dialog()


# Backward-compatible alias for existing imports/callers.
QualityImprovementDialog = OpenClawAgentDialog


__all__ = [
    "show_extract_features_dialog",
    "show_plan_operations_dialog",
    "show_workflow_dialog",
    "show_generate_nc_dialog",
    "show_fuse_ops_mesh_dialog",
    "show_openclaw_agent_dialog",
    "show_quality_improvement_dialog",
]
