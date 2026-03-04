"""WAAM assistant for generating and refining NC programs via a local LLM."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .qt_compat import QtCore, QtGui, QtWidgets

from ..core.config_loader import resolve_standard_config_path
from ..core.paths import (
    get_nc_output_path,
    get_operations_path,
    get_operations_with_mesh_path,
    get_output_dir,
)
from ..llm_agent import (
    PlanToNcResult,
    SpecLoadError,
    run_nc_refinement,
)
from ..llm_agent.ollama_client import _DEFAULT_NC_MODEL
from ..waamgen.pipeline import load_light_job, validate_gcode
from .ui_constants import DEFAULT_DIALOG_SIZE

_KNOWN_NC_MODELS = ("gpt-oss:latest", "llama3:latest", "deepseek-coder:6.7b")
_NC_REFINEMENT_CONTRACT = (
    "CONTRACT: Preserve geometry and frame: keep all layer Z values, path IDs, and WCS from the WAAM JSON. "
    "Do not invent paths. Use only provided torch/gas/wire commands (M62/M63/M64/M65/M68) and stay within machine feeds. "
    "Never rapid (G0) with torch on. You may tune feedrates, hatch angles per layer, lead-in/out length, start points, "
    "and smooth corners/links. Emit full LinuxCNC G-code only."
)


def _default_json_path() -> Path:
    fused = get_operations_with_mesh_path(prefer_existing=True)
    if fused.is_file():
        return fused
    return get_operations_path(prefer_existing=True)


def _default_baseline_path() -> Path:
    return get_nc_output_path(prefer_existing=False, create=True)


def _latest_mesh_txt() -> Optional[Path]:
    target_dir = get_output_dir() / "mesh_exports"
    if not target_dir.is_dir():
        return None
    patterns = ("*.mesh_for_llm", "*.mesh_for_llm.txt", "*.txt", "*.obj")
    for pattern in patterns:
        candidates = sorted(target_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0]
    return None


class LLMAssistantDialog(QtWidgets.QDialog):
    """WAAM assistant for rebuilding and refining NC programs with mesh-aware context."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WAAM - NC Assistant")
        self.resize(*DEFAULT_DIALOG_SIZE)
        self.setMinimumSize(*DEFAULT_DIALOG_SIZE)

        self.current_nc_program: Optional[str] = None
        self.refine_history: List[dict] = []

        self._config_path = self._resolve_config_path()

        context_group = QtWidgets.QGroupBox("Shared context", self)
        context_layout = QtWidgets.QVBoxLayout(context_group)
        latest_mesh = _latest_mesh_txt()
        self.mesh_path_edit = QtWidgets.QLineEdit(str(latest_mesh) if latest_mesh else "", self)
        self.mesh_path_edit.setPlaceholderText("part.mesh_for_llm")
        mesh_browse = QtWidgets.QPushButton("Browse…", self)
        mesh_browse.clicked.connect(self._choose_mesh_context)
        mesh_row = QtWidgets.QHBoxLayout()
        mesh_row.addWidget(QtWidgets.QLabel("Mesh context (.mesh_for_llm/.txt/.obj):", self))
        mesh_row.addWidget(self.mesh_path_edit, 1)
        mesh_row.addWidget(mesh_browse)
        context_layout.addLayout(mesh_row)

        self.model_combo = QtWidgets.QComboBox(self)
        self.model_combo.setEditable(True)
        for model_name in _KNOWN_NC_MODELS:
            self.model_combo.addItem(model_name)
        self.model_combo.setCurrentText(_DEFAULT_NC_MODEL)
        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(QtWidgets.QLabel("NC model:", self))
        model_row.addWidget(self.model_combo, 1)
        context_layout.addLayout(model_row)

        self.config_label = QtWidgets.QLabel(self._config_display_text(), self)
        self.config_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.config_label.setToolTip("Resolved standard_waam.json location.")
        config_row = QtWidgets.QHBoxLayout()
        config_row.addWidget(QtWidgets.QLabel("WAAM config (standard_waam.json):", self))
        config_row.addWidget(self.config_label, 1)
        context_layout.addLayout(config_row)

        tabs = QtWidgets.QTabWidget(self)
        tabs.setDocumentMode(True)
        tabs.addTab(self._build_rebuild_tab(), "Step 1 - Rebuild NC")
        tabs.addTab(self._build_refine_tab(), "Step 2 - Refine NC")

        # ----------------------------- Output panes -----------------------------
        self.status_label = QtWidgets.QLabel("Ready.", self)
        self.status_label.setWordWrap(True)

        self.nc_preview = QtWidgets.QPlainTextEdit(self)
        self.nc_preview.setReadOnly(True)

        self.save_nc_button = QtWidgets.QPushButton("Save NC…", self)
        self.save_nc_button.setEnabled(False)
        self.save_nc_button.clicked.connect(self._save_nc)

        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.nc_preview.setFont(fixed_font)
        self.nc_preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

        preview_header = QtWidgets.QHBoxLayout()
        preview_header.addWidget(QtWidgets.QLabel("NC program preview:", self))
        preview_header.addStretch(1)
        preview_header.addWidget(self.save_nc_button)

        self.output_log = QtWidgets.QTextEdit(self)
        self.output_log.setReadOnly(True)
        self.output_log.setFont(fixed_font)
        self.output_log.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)

        preview_container = QtWidgets.QWidget(self)
        preview_layout = QtWidgets.QVBoxLayout(preview_container)
        preview_layout.addLayout(preview_header)
        preview_layout.addWidget(self.nc_preview)

        log_container = QtWidgets.QWidget(self)
        log_layout = QtWidgets.QVBoxLayout(log_container)
        log_layout.addWidget(QtWidgets.QLabel("Assistant log:", self))
        log_layout.addWidget(self.output_log)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(preview_container)
        splitter.addWidget(log_container)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(context_group)
        layout.addWidget(tabs, 1)
        layout.addWidget(splitter, 2)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    def _build_rebuild_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(tab)

        baseline_row = QtWidgets.QHBoxLayout()
        self.baseline_path_edit = QtWidgets.QLineEdit(str(_default_baseline_path()), self)
        baseline_browse = QtWidgets.QPushButton("Browse…", self)
        baseline_browse.clicked.connect(self._browse_baseline)
        baseline_row.addWidget(QtWidgets.QLabel("waam_baseline.nc:", self))
        baseline_row.addWidget(self.baseline_path_edit, 1)
        baseline_row.addWidget(baseline_browse)
        layout.addLayout(baseline_row)

        self.rebuild_request_edit = QtWidgets.QPlainTextEdit(self)
        self.rebuild_request_edit.setPlaceholderText(
            "Optional instructions for regenerating the baseline NC…\n"
            "Leave blank to simply re-run the baseline through the LLM with the mesh context."
        )
        layout.addWidget(QtWidgets.QLabel("Optional instruction:", self))
        layout.addWidget(self.rebuild_request_edit, 1)

        buttons_row = QtWidgets.QHBoxLayout()
        self.validate_button = QtWidgets.QPushButton("Validate baseline vs fused JSON", self)
        self.validate_button.clicked.connect(self._run_validate_current)
        self.rebuild_button = QtWidgets.QPushButton("Regenerate NC", self)
        self.rebuild_button.clicked.connect(self._run_rebuild)
        buttons_row.addWidget(self.validate_button)
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.rebuild_button)
        layout.addLayout(buttons_row)
        return tab

    def _build_refine_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(tab)

        self.load_nc_button = QtWidgets.QPushButton("Load existing NC…", self)
        self.load_nc_button.clicked.connect(self._load_nc_from_file)
        load_row = QtWidgets.QHBoxLayout()
        load_row.addWidget(self.load_nc_button)
        load_row.addStretch(1)
        layout.addLayout(load_row)

        self.change_request_edit = QtWidgets.QPlainTextEdit(self)
        self.change_request_edit.setPlaceholderText("Describe the changes you want to discuss with the LLM…")
        layout.addWidget(QtWidgets.QLabel("Change request:", self))
        layout.addWidget(self.change_request_edit, 1)

        self.refine_button = QtWidgets.QPushButton("Refine current NC", self)
        self.refine_button.clicked.connect(self._run_refine)
        layout.addWidget(self.refine_button, alignment=QtCore.Qt.AlignRight)
        return tab

    # ------------------------------------------------------------------ Helpers
    def _choose_mesh_context(self) -> None:
        start = self.mesh_path_edit.text().strip() or str(get_output_dir() / "mesh_exports")
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select mesh context file",
            start,
            "Mesh context (*.mesh_for_llm *.mesh_for_llm.txt *.txt *.obj);;All files (*.*)",
        )
        if path:
            self.mesh_path_edit.setText(path)

    def _browse_baseline(self) -> None:
        start = self.baseline_path_edit.text().strip() or str(_default_baseline_path())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select baseline NC file",
            start,
            "NC files (*.nc *.tap *.gcode);;All files (*.*)",
        )
        if path:
            self.baseline_path_edit.setText(path)

    def _load_nc_from_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select NC file",
            str(Path.home()),
            "NC files (*.nc *.tap *.gcode);;All files (*.*)",
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Unable to read NC file:\n{exc}")
            return
        self._set_nc_program(text)
        self.refine_history.clear()
        self._append("nc", f"Loaded NC program: {path}")
        self._set_status(f"Loaded NC program from {path}")

    def _save_nc(self) -> None:
        if not self.current_nc_program:
            QtWidgets.QMessageBox.information(self, "WAAM", "No NC program available to save.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save NC Program",
            str(Path.home() / "waam_program.nc"),
            "NC files (*.nc);;All files (*.*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.current_nc_program, encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Failed to save NC:\n{exc}")
            return
        QtWidgets.QMessageBox.information(self, "WAAM", f"NC program saved to:\n{path}")
        self._set_status(f"NC program saved to {path}")

    def _validate_refine_inputs(self) -> Optional[tuple[str, str]]:
        json_path = str(_default_json_path())
        mesh_path = self.mesh_path_edit.text().strip()
        if not Path(json_path).is_file():
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                f"WAAM operations JSON not found:\n{json_path}\nRun WAAM: Plan Operations first.",
            )
            return None
        if not mesh_path:
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                "Select a mesh context (.mesh_for_llm/.txt/.obj) file first.",
            )
            return None
        if not Path(mesh_path).is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"Mesh file not found:\n{mesh_path}")
            return None
        return json_path, mesh_path

    def _run_validate_current(self) -> None:
        json_path = str(_default_json_path())
        if not Path(json_path).is_file():
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                f"WAAM operations JSON not found:\n{json_path}\nRun WAAM: Fuse Plan + Mesh first.",
            )
            return

        program_text: Optional[str] = None
        if self.current_nc_program and self.current_nc_program.strip():
            program_text = self.current_nc_program
        else:
            baseline_path = self.baseline_path_edit.text().strip()
            if baseline_path and Path(baseline_path).is_file():
                try:
                    program_text = Path(baseline_path).read_text(encoding="utf-8")
                except OSError as exc:
                    QtWidgets.QMessageBox.critical(self, "WAAM", f"Unable to read NC file:\n{exc}")
                    return
        if not program_text:
            QtWidgets.QMessageBox.information(self, "WAAM", "Load an NC program or select waam_baseline.nc first.")
            return

        ok = self._validate_nc(program_text, json_path)
        if ok is True:
            self._set_status("Validation PASS.")
        elif ok is False:
            self._set_status("Validation FAIL (see log).")
        else:
            self._set_status("Validation skipped.")

    def _handle_result(self, label: str, result: PlanToNcResult, ref_json: Optional[str] = None, geom_tol: float = 0.8) -> None:
        if result.parsed_json:
            summary = result.parsed_json.get("summary")
            warnings = result.parsed_json.get("warnings")
            if summary:
                self._append(label, f"Summary: {summary}")
            if warnings:
                self._append(label, f"Warnings: {warnings}")
        else:
            self._append(label, result.raw_output or "[Empty response]")

        if result.gcode:
            self._set_nc_program(result.gcode)
            self.save_nc_button.setEnabled(True)
            preview = preview_gcode(result.gcode)
            self._append("gcode", preview)
            self._set_status("NC program ready.")
            if ref_json:
                self._validate_nc(result.gcode, ref_json, tol=geom_tol)
        else:
            self._append("error", "No NC program returned.")
            self._set_status("LLM response did not include NC output.")

    def _set_nc_program(self, text: str) -> None:
        self.current_nc_program = text
        self.nc_preview.setPlainText(text)
        self.save_nc_button.setEnabled(bool(text.strip()))

    def _append(self, prefix: str, text: str) -> None:
        self.output_log.append(f"[{prefix}] {text}")
        bar = self.output_log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _resolve_config_path(self) -> Optional[Path]:
        try:
            return resolve_standard_config_path()
        except FileNotFoundError:
            return None

    def _validate_nc(self, gcode_text: str, ref_json: str, tol: float = 0.8) -> Optional[bool]:
        try:
            job = load_light_job(ref_json)
        except Exception as exc:
            self._append("validate", f"Validation skipped ({exc})")
            return None
        try:
            report = validate_gcode(gcode_text, job, geom_tol=tol)
        except Exception as exc:  # pragma: no cover - defensive
            self._append("validate", f"Validation failed ({exc})")
            return None

        status = "PASS" if report.ok else "FAIL"
        details = "; ".join(report.messages) if report.messages else "No issues."
        self._append("validate", f"{status} syntax={report.syntax_ok} safety={report.safety_ok} geometry={report.geometry_ok} :: {details}")
        return report.ok

    def _config_display_text(self) -> str:
        return str(self._config_path) if self._config_path else "[Not found]"

    def _selected_model(self) -> str:
        text = self.model_combo.currentText().strip()
        return text or _DEFAULT_NC_MODEL

    # ------------------------------------------------------------------ Actions
    def _run_rebuild(self) -> None:
        validated = self._validate_refine_inputs()
        if not validated:
            return
        json_path, mesh_path = validated
        baseline_path = self.baseline_path_edit.text().strip()
        if not baseline_path:
            QtWidgets.QMessageBox.warning(self, "WAAM", "Select a waam_baseline.nc file first.")
            return
        baseline_file = Path(baseline_path)
        if not baseline_file.is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"Baseline NC not found:\n{baseline_path}")
            return
        try:
            baseline_text = baseline_file.read_text(encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Unable to read baseline NC:\n{exc}")
            return

        change_request = self.rebuild_request_edit.toPlainText().strip()
        if not change_request:
            change_request = (
                "Rebuild the supplied WAAM baseline NC using the deterministic WAAM JSON and mesh context. "
                "Preserve machine/WCS constraints and emit a complete program."
            )

        model = self._selected_model()
        self._set_status(f"Regenerating NC with {model}…")

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            result = run_nc_refinement(
                model=model,
                json_path=json_path,
                mesh_path=mesh_path,
                config_path=str(self._config_path) if self._config_path else None,
                base_program=baseline_text,
                change_request=change_request,
                history=[],
                extra_instructions=_NC_REFINEMENT_CONTRACT,
            )
        except SpecLoadError as exc:
            QtWidgets.QMessageBox.warning(self, "WAAM", str(exc))
            self._set_status("Regeneration aborted.")
            return
        except Exception as exc:  # pragma: no cover
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Regeneration failed:\n{exc}")
            self._set_status("Regeneration failed.")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.refine_history = []
        self._append("rebuild", f"Regenerated NC from {baseline_path}")
        self._handle_result("rebuild", result, ref_json=json_path)

    def _run_refine(self) -> None:
        validated = self._validate_refine_inputs()
        if not validated:
            return
        if not self.current_nc_program:
            QtWidgets.QMessageBox.information(self, "WAAM", "Load a baseline NC program first.")
            return
        change_request = self.change_request_edit.toPlainText().strip()
        if not change_request:
            QtWidgets.QMessageBox.information(self, "WAAM", "Describe the change you need.")
            return

        json_path, mesh_path = validated
        history_snapshot = list(self.refine_history)
        model = self._selected_model()
        self._set_status(f"Refining NC with {model}…")

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            result = run_nc_refinement(
                model=model,
                json_path=json_path,
                mesh_path=mesh_path,
                config_path=str(self._config_path) if self._config_path else None,
                base_program=self.current_nc_program,
                change_request=change_request,
                history=history_snapshot,
                extra_instructions=_NC_REFINEMENT_CONTRACT,
            )
        except SpecLoadError as exc:
            QtWidgets.QMessageBox.warning(self, "WAAM", str(exc))
            self._set_status("Refinement aborted.")
            return
        except Exception as exc:  # pragma: no cover
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Refinement failed:\n{exc}")
            self._set_status("Refinement failed.")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.refine_history.append({"role": "user", "content": change_request})
        if result.parsed_json and result.parsed_json.get("summary"):
            self.refine_history.append({"role": "assistant", "content": result.parsed_json["summary"]})
        elif result.raw_output:
            self.refine_history.append({"role": "assistant", "content": result.raw_output[:500]})

        self._handle_result("refine", result, ref_json=json_path)


def preview_gcode(gcode: str, lines: int = 40) -> str:
    all_lines = gcode.splitlines()
    snippet = all_lines[:lines]
    tail = "\n..." if len(all_lines) > lines else ""
    return "\n".join(snippet) + tail


_ACTIVE_DIALOG: Optional[LLMAssistantDialog] = None


def show_llm_dialog() -> None:
    global _ACTIVE_DIALOG

    if _ACTIVE_DIALOG is not None:
        try:
            if _ACTIVE_DIALOG.isVisible():
                _ACTIVE_DIALOG.raise_()
                _ACTIVE_DIALOG.activateWindow()
                return
        except RuntimeError:
            _ACTIVE_DIALOG = None

    dlg = LLMAssistantDialog()
    dlg.setModal(False)
    dlg.setWindowModality(QtCore.Qt.NonModal)

    def _dialog_finished(_result: int) -> None:
        global _ACTIVE_DIALOG
        _ACTIVE_DIALOG = None

    def _dialog_destroyed(_obj=None) -> None:
        global _ACTIVE_DIALOG
        _ACTIVE_DIALOG = None

    dlg.finished.connect(_dialog_finished)
    dlg.destroyed.connect(_dialog_destroyed)
    dlg.show()
    _ACTIVE_DIALOG = dlg


__all__ = ["show_llm_dialog"]
