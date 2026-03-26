"""Mission Control panel for the WAAM workbench.

A lightweight workflow-first control surface that reuses the existing WAAM
pipeline dialogs instead of duplicating their core logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from .qt_compat import QtCore, QtGui, QtWidgets
from ..core.paths import (
    get_features_path,
    get_nc_output_path,
    get_operations_path,
    get_operations_with_mesh_path,
    get_output_dir,
)
from .ui_validator import ValidationWidget
from .wcs_marker import get_stored_wcs, get_wcs_label
from .ui_workflow import (
    show_extract_features_dialog,
    show_fuse_ops_mesh_dialog,
    show_generate_nc_dialog,
    show_plan_operations_dialog,
    show_workflow_dialog,
)
from .torch_path_viewer import show_torch_path_viewer

_ACTIVE_DIALOG: Optional["MissionControlDialog"] = None
_PREVIEWABLE_SUFFIXES = {
    ".json",
    ".nc",
    ".tap",
    ".gcode",
    ".txt",
    ".waam",
    ".llm_waam",
}


class MissionControlDialog(QtWidgets.QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setModal(False)
        self.setWindowModality(QtCore.Qt.NonModal)
        self.setWindowTitle("WAAM - Mission Control")
        self.resize(1080, 760)
        self.setMinimumSize(900, 620)
        self._artifact_labels: list[tuple[QtWidgets.QLabel, Path]] = []
        self._build_ui()
        self.refresh_status()

    def _raise_panel(self) -> None:
        try:
            if self.isMinimized():
                self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()
        except RuntimeError:
            pass

    def _raise_panel_later(self) -> None:
        self._raise_panel()
        QtCore.QTimer.singleShot(0, self._raise_panel)
        QtCore.QTimer.singleShot(50, self._raise_panel)

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)

        header = QtWidgets.QLabel(
            "Workflow-first mission control for the deterministic WAAM pipeline.", self
        )
        header.setWordWrap(True)
        root.addWidget(header)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        root.addWidget(splitter, 1)

        left = QtWidgets.QWidget(self)
        left_layout = QtWidgets.QVBoxLayout(left)
        splitter.addWidget(left)

        right = QtWidgets.QWidget(self)
        right_layout = QtWidgets.QVBoxLayout(right)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self._build_overview(left_layout)
        self._build_pipeline(left_layout)
        self._build_artifacts(left_layout)
        self._build_validation(right_layout)
        self._build_agents(right_layout)
        self._build_logs(root)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        refresh_button = buttons.addButton("Refresh", QtWidgets.QDialogButtonBox.ActionRole)
        refresh_button.clicked.connect(self.refresh_status)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_overview(self, parent_layout: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("Overview", self)
        form = QtWidgets.QFormLayout(group)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        self.doc_value = QtWidgets.QLabel("-", self)
        self.wcs_value = QtWidgets.QLabel("-", self)
        self.deposition_value = QtWidgets.QLabel("-", self)
        self.risk_value = QtWidgets.QLabel("High attention: WCS / deposition_start / schema drift", self)
        self.risk_value.setWordWrap(True)

        form.addRow("Active document:", self.doc_value)
        form.addRow("Current WCS:", self.wcs_value)
        form.addRow("Deposition start:", self.deposition_value)
        form.addRow("Risk level:", self.risk_value)
        parent_layout.addWidget(group)

    def _build_pipeline(self, parent_layout: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("Pipeline", self)
        layout = QtWidgets.QGridLayout(group)

        actions = [
            ("Set WCS", self._open_wcs),
            ("Export STEP", self._export_step),
            ("Open workflow dialog", self._open_workflow),
            ("Run feature extraction", self._open_extract),
            ("Run deterministic planner", self._open_plan),
            ("Generate baseline NC", self._open_generate_nc),
            ("Fuse ops + mesh", self._open_fuse_ops_mesh),
            ("Open torch path viewer", self._open_torch_viewer),
        ]

        for idx, (label, callback) in enumerate(actions):
            btn = QtWidgets.QPushButton(label, self)
            btn.clicked.connect(callback)
            layout.addWidget(btn, idx // 2, idx % 2)

        parent_layout.addWidget(group)

    def _build_artifacts(self, parent_layout: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("Artifacts", self)
        layout = QtWidgets.QGridLayout(group)

        output_dir = get_output_dir()
        artifact_paths = [
            ("features.json", get_features_path(output_dir, create=True, prefer_existing=True)),
            ("waam_operations.json", get_operations_path(output_dir, create=True, prefer_existing=True)),
            ("fused operations+mesh", get_operations_with_mesh_path(output_dir, create=True, prefer_existing=True)),
            ("baseline NC", get_nc_output_path(output_dir, create=True, prefer_existing=True)),
            ("output folder", output_dir),
        ]

        for row, (label_text, path) in enumerate(artifact_paths):
            label = QtWidgets.QLabel(label_text + ":", self)
            value = QtWidgets.QLabel(self._artifact_text(path), self)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            button = QtWidgets.QPushButton("Open", self)
            button.clicked.connect(lambda _=False, p=path: self._open_path(p))
            self._artifact_labels.append((value, path))
            layout.addWidget(label, row, 0)
            layout.addWidget(value, row, 1)
            layout.addWidget(button, row, 2)

        parent_layout.addWidget(group)

    def _build_validation(self, parent_layout: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("Validation", self)
        layout = QtWidgets.QVBoxLayout(group)
        self.validation_hint = QtWidgets.QLabel(
            "Loads the latest *.llm_waam / *.waam file from output/dsl_waam when available.", self
        )
        self.validation_hint.setWordWrap(True)
        self.validation_widget = ValidationWidget(self)
        load_button = QtWidgets.QPushButton("Refresh validation preview", self)
        load_button.clicked.connect(self._load_validation_preview)
        layout.addWidget(self.validation_hint)
        layout.addWidget(load_button, alignment=QtCore.Qt.AlignRight)
        layout.addWidget(self.validation_widget, 1)
        parent_layout.addWidget(group, 1)

    def _build_agents(self, parent_layout: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("Agents", self)
        layout = QtWidgets.QVBoxLayout(group)
        text = (
            "Canonical team structure is maintained in:\n"
            "/Users/sustian/.openclaw/workspace/agents/\n\n"
            "Active roles:\n"
            "- agentwaam — public-facing orchestrator / systems integrator\n"
            "- oliveratonbot — technical triage lead / internal analyst\n"
            "- marklendersbot — FreeCAD / geometry / CAD-CAM specialist\n"
            "- benjipricebot — critic / reviewer\n\n"
            "Notes:\n"
            "- This panel is informational only.\n"
            "- Do not treat old embedded WAAM mission-team files as canonical.\n"
            "- Part-specific planner experiments should live under Waam_tech/experimental/."
        )
        label = QtWidgets.QLabel(text, self)
        label.setWordWrap(True)
        layout.addWidget(label)
        parent_layout.addWidget(group)

    def _build_logs(self, parent_layout: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("Logs", self)
        layout = QtWidgets.QVBoxLayout(group)
        self.log_box = QtWidgets.QPlainTextEdit(self)
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(500)
        layout.addWidget(self.log_box)
        parent_layout.addWidget(group)

    def _log(self, message: str) -> None:
        self.log_box.appendPlainText(message)

    def refresh_status(self) -> None:
        doc = App.ActiveDocument
        self.doc_value.setText(getattr(doc, "Label", None) or "[None]")
        if doc is None:
            self.wcs_value.setText("[No active document]")
            self.deposition_value.setText("[Unavailable]")
        else:
            self.wcs_value.setText(get_wcs_label(doc) or "[Unset]")
            placement = get_stored_wcs(doc)
            self.deposition_value.setText("Stored with document WCS" if placement is not None else "[Unset]")

        for label, path in self._artifact_labels:
            label.setText(self._artifact_text(path))

        self._load_validation_preview(silent=True)
        self._log("Mission Control refreshed.")

    def _artifact_text(self, path: Path) -> str:
        try:
            if path.is_file() or path.is_dir():
                return f"Ready — {path}"
        except Exception:
            pass
        return f"Missing — {path}"

    def _open_path(self, path: Path) -> None:
        try:
            if not path.exists():
                raise FileNotFoundError(path)
            if path.is_file() and path.suffix.lower() in _PREVIEWABLE_SUFFIXES:
                self._open_text_preview(path)
                return

            opened = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
            if not opened:
                raise RuntimeError(f"No application handled: {path}")
            self._log(f"Opened artifact path: {path}")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "WAAM", f"Could not open path:\n{path}\n\n{exc}")

    def _open_text_preview(self, path: Path) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "WAAM", f"Could not read file:\n{path}\n\n{exc}")
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"WAAM Artifact Preview - {path.name}")
        dialog.resize(960, 640)

        layout = QtWidgets.QVBoxLayout(dialog)

        path_label = QtWidgets.QLabel(str(path), dialog)
        path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        editor = QtWidgets.QPlainTextEdit(dialog)
        editor.setReadOnly(True)
        editor.setPlainText(content)
        layout.addWidget(editor, 1)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        self._log(f"Opened artifact preview for {path}")
        dialog.exec_()

    def _open_wcs(self) -> None:
        from .ui_wcs import show_wcs_dialog

        try:
            show_wcs_dialog(parent=self)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Failed to run Set WCS:\n{exc}")
            return
        self.refresh_status()
        self._raise_panel_later()

    def _export_step(self) -> None:
        try:
            Gui.runCommand("Waam_ExportStep", 0)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Failed to run Export STEP:\n{exc}")
            return
        self.refresh_status()
        self._raise_panel_later()

    def _open_workflow(self) -> None:
        show_workflow_dialog(parent=self)
        self.refresh_status()
        self._raise_panel_later()

    def _open_extract(self) -> None:
        show_extract_features_dialog(parent=self)
        self.refresh_status()
        self._raise_panel_later()

    def _open_plan(self) -> None:
        show_plan_operations_dialog(parent=self)
        self.refresh_status()
        self._raise_panel_later()

    def _open_generate_nc(self) -> None:
        show_generate_nc_dialog(parent=self)
        self.refresh_status()
        self._raise_panel_later()

    def _open_fuse_ops_mesh(self) -> None:
        show_fuse_ops_mesh_dialog(parent=self)
        self.refresh_status()
        self._raise_panel_later()

    def _open_torch_viewer(self) -> None:
        start_dir = str(get_output_dir() / "nc_files")
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select WAAM NC or plan file",
            start_dir,
            "WAAM files (*.nc *.tap *.gcode *.json);;All files (*.*)",
        )
        if not file_path:
            return
        show_torch_path_viewer(file_path)
        self._log(f"Opened torch path viewer for {file_path}")

    def _load_validation_preview(self, silent: bool = False) -> None:
        dsl_dir = get_output_dir() / "dsl_waam"
        candidate = self._latest_matching_file(dsl_dir, ("*.llm_waam", "*.waam", "*.txt"))
        if candidate is None:
            self.validation_hint.setText(
                "No validation DSL file found in output/dsl_waam yet. Generate one, then refresh."
            )
            return
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")
            self.validation_widget.load_dsl(content)
            self.validation_hint.setText(f"Loaded validation preview from: {candidate}")
            if not silent:
                self._log(f"Loaded validation preview from {candidate}")
        except Exception as exc:
            self.validation_hint.setText(f"Validation preview failed: {exc}")
            if not silent:
                self._log(f"Validation preview failed: {exc}")

    def _latest_matching_file(self, root: Path, patterns: Iterable[str]) -> Optional[Path]:
        if not root.is_dir():
            return None
        matches: list[Path] = []
        for pattern in patterns:
            matches.extend(root.glob(pattern))
        if not matches:
            return None
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return matches[0]


def _parent_window():
    return Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None


def show_mission_control_dialog():
    global _ACTIVE_DIALOG

    if _ACTIVE_DIALOG is not None:
        try:
            _ACTIVE_DIALOG.refresh_status()
            _ACTIVE_DIALOG.show()
            _ACTIVE_DIALOG.raise_()
            _ACTIVE_DIALOG.activateWindow()
            return
        except RuntimeError:
            _ACTIVE_DIALOG = None

    dlg = MissionControlDialog(parent=_parent_window())

    def _dialog_finished(_result: int) -> None:
        global _ACTIVE_DIALOG
        _ACTIVE_DIALOG = None

    def _dialog_destroyed(_obj=None) -> None:
        global _ACTIVE_DIALOG
        _ACTIVE_DIALOG = None

    dlg.finished.connect(_dialog_finished)
    dlg.destroyed.connect(_dialog_destroyed)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    _ACTIVE_DIALOG = dlg


__all__ = ["MissionControlDialog", "show_mission_control_dialog"]
