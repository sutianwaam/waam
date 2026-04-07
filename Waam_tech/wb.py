from pathlib import Path as SysPath

import FreeCADGui as Gui

_ICON_PATH = str((SysPath(__file__).resolve().parent / "icons" / "waam.svg"))
_SHUTDOWN_HOOK_INSTALLED = False


def _close_waam_windows():
    try:
        from .ui.qt_compat import QtWidgets
    except Exception:
        return

    app = QtWidgets.QApplication.instance()
    if app is None:
        return

    for widget in list(app.topLevelWidgets()):
        try:
            title = str(widget.windowTitle() or "")
        except Exception:
            continue
        if not title.startswith("WAAM"):
            continue
        try:
            widget.close()
        except Exception:
            pass
        try:
            widget.deleteLater()
        except Exception:
            pass


def _install_shutdown_hook():
    global _SHUTDOWN_HOOK_INSTALLED
    if _SHUTDOWN_HOOK_INSTALLED:
        return

    try:
        from .ui.qt_compat import QtWidgets
    except Exception:
        return

    app = QtWidgets.QApplication.instance()
    if app is None:
        return

    app.aboutToQuit.connect(_close_waam_windows)
    _SHUTDOWN_HOOK_INSTALLED = True


class WaamWorkbench(Gui.Workbench):
    MenuText = "WAAM"
    ToolTip = "AI-assisted WAAM with Mission Control"
    Icon = _ICON_PATH if SysPath(_ICON_PATH).is_file() else ""

    def Initialize(self):
        # Lazy import to avoid GUI pointer errors during FreeCAD startup
        from .ui import commands as cmds
        _install_shutdown_hook()
        self.appendToolbar("WAAM", cmds.COMMANDS)
        self.appendMenu("W&AAM", cmds.MENU)

    def GetClassName(self):
        return "Gui::PythonWorkbench"
