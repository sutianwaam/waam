from pathlib import Path as SysPath

import FreeCADGui as Gui

_ICON_PATH = str((SysPath(__file__).resolve().parent / "icons" / "waam.svg"))


class WaamWorkbench(Gui.Workbench):
    MenuText = "WAAM"
    ToolTip = "AI-assisted WAAM"
    Icon = _ICON_PATH if SysPath(_ICON_PATH).is_file() else ""

    def Initialize(self):
        # Lazy import to avoid GUI pointer errors during FreeCAD startup
        from .ui import commands as cmds
        self.appendToolbar("WAAM", cmds.COMMANDS)
        self.appendMenu("WAAM", cmds.MENU)

    def GetClassName(self):
        return "Gui::PythonWorkbench"
