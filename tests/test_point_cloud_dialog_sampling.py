from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace

from _fc_import_shims import _install_freecad_stubs


def _install_qt_stubs(monkeypatch):
    pyside = types.ModuleType("PySide6")
    qtcore = types.ModuleType("PySide6.QtCore")
    qtgui = types.ModuleType("PySide6.QtGui")
    qtwidgets = types.ModuleType("PySide6.QtWidgets")

    class DummyDialog:
        pass

    qtcore.Qt = SimpleNamespace(WA_DeleteOnClose=0)
    qtwidgets.QDialog = DummyDialog
    pyside.QtCore = qtcore
    pyside.QtGui = qtgui
    pyside.QtWidgets = qtwidgets

    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)
    monkeypatch.setitem(sys.modules, "PySide6.QtGui", qtgui)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qtwidgets)


def _load_dialog_module(monkeypatch):
    _install_freecad_stubs()
    _install_qt_stubs(monkeypatch)
    sys.modules.pop("Waam_tech.ui.qt_compat", None)
    sys.modules.pop("Waam_tech.ui.point_cloud_dialog", None)
    return importlib.import_module("Waam_tech.ui.point_cloud_dialog")


def test_default_spacing_is_bbox_based_and_twenty_percent_denser(monkeypatch):
    dialog = _load_dialog_module(monkeypatch)

    assert dialog._default_spacing_from_dimensions((100.0, 20.0, 10.0)) == 4.0
    assert dialog._default_spacing_from_dimensions(None) == 4.0


def test_volume_mode_generates_interior_points(monkeypatch):
    dialog = _load_dialog_module(monkeypatch)

    class BoxShape:
        BoundBox = SimpleNamespace(XMin=0.0, XMax=2.0, YMin=0.0, YMax=2.0, ZMin=0.0, ZMax=2.0)
        Solids = [object()]
        Vertexes = []
        Edges = []

        def isInside(self, point, _tol=0.0, _strict=False):
            return 0.0 <= point.x <= 2.0 and 0.0 <= point.y <= 2.0 and 0.0 <= point.z <= 2.0

        def tessellate(self, _spacing):
            return ([], [])

    obj = SimpleNamespace(Name="box", Label="box", Shape=BoxShape())
    rows, _preview_points, truncated = dialog._collect_rows(
        [obj],
        placement=dialog.App.Placement(),
        spacing=1.0,
        mode="volume",
        max_points=100,
        include_world=True,
    )

    assert truncated is False
    assert any(row["x_mm"] == 1.0 and row["y_mm"] == 1.0 and row["z_mm"] == 1.0 for row in rows)
