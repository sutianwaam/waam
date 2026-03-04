# InitGui.py
from __future__ import annotations

import sys
import traceback
from pathlib import Path as SysPath

import FreeCAD as App

_PACKAGE_CANDIDATES = ("Waam_tech", "Waam")


def _module_dir() -> SysPath:
    """Return the directory this module lives in, with fallbacks for odd loaders."""
    try:
        return SysPath(__file__).resolve().parent
    except Exception:
        mod = sys.modules.get(__name__)
        path = getattr(mod, "__file__", None)
        if path:
            try:
                return SysPath(path).resolve().parent
            except Exception:
                pass
    return SysPath.cwd()


def _find_package_dir(module_dir: SysPath) -> SysPath:
    """Return the on-disk directory that contains the workbench package."""
    for name in _PACKAGE_CANDIDATES:
        candidate = module_dir / name
        if candidate.is_dir():
            return candidate
    return module_dir / _PACKAGE_CANDIDATES[0]


def _ensure_package_on_path() -> SysPath:
    """Make sure the module, parent, and package directories are importable."""
    path_cls = globals().get("SysPath") or __import__("pathlib").Path
    module_dir_fn = globals().get("_module_dir")
    if callable(module_dir_fn):
        module_dir = module_dir_fn()
    else:
        try:
            module_dir = path_cls(__file__).resolve().parent
        except Exception:
            module_dir = path_cls.cwd()
    package_dir = _find_package_dir(module_dir)
    parent_dir = module_dir.parent

    for path in (parent_dir, module_dir, package_dir):
        resolved = str(path)
        if path.exists() and resolved not in sys.path:
            sys.path.insert(0, resolved)

    return package_dir


def _is_registered(gui_module) -> bool:
    list_fn = getattr(gui_module, "listWorkbenches", None)
    if not list_fn:
        return False
    wb_map = list_fn()
    return "WaamWorkbench" in wb_map or "WAAM" in wb_map


def _register_workbench() -> None:
    try:
        import FreeCADGui as Gui
    except Exception as exc:
        try:
            App.Console.PrintWarning(f"[WAAM] GUI not ready; will retry when FreeCAD reloads InitGui: {exc}\n")
        except Exception:
            pass
        return

    try:
        # Defensive: if globals weren't initialized (e.g., partial reload), recompute paths inline.
        helper = globals().get("_ensure_package_on_path")
        if callable(helper):
            package_dir = helper()
        else:
            path_cls = globals().get("SysPath") or __import__("pathlib").Path
            module_dir_fn = globals().get("_module_dir")
            if callable(module_dir_fn):
                module_dir = module_dir_fn()
            else:
                try:
                    module_dir = path_cls(__file__).resolve().parent
                except Exception:
                    module_dir = path_cls.cwd()
            finder = globals().get("_find_package_dir")
            package_dir = finder(module_dir) if callable(finder) else module_dir
            for path in (module_dir.parent, module_dir, package_dir):
                resolved = str(path)
                if path_cls(path).exists() and resolved not in sys.path:
                    sys.path.insert(0, resolved)

        try:
            from Waam_tech.wb import WaamWorkbench
        except ImportError:
            from Waam.wb import WaamWorkbench

        is_registered_fn = globals().get("_is_registered")
        if callable(is_registered_fn) and is_registered_fn(Gui):
            return

        icon_dir = package_dir / "icons"
        if icon_dir.is_dir():
            try:
                Gui.addIconPath(str(icon_dir))
            except Exception:
                pass

        Gui.addWorkbench(WaamWorkbench())
        try:
            App.Console.PrintMessage("[WAAM] Workbench registered.\n")
        except Exception:
            pass
    except Exception as exc:  # pragma: no cover - startup diagnostics
        try:
            App.Console.PrintError(f"[WAAM] Failed to register workbench: {exc}\n")
            App.Console.PrintError(traceback.format_exc())
        except Exception:
            pass


# Register immediately; FreeCAD calls InitGui after GUI is up.
_register_workbench()
