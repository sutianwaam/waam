from __future__ import annotations

import inspect
import sys
from pathlib import Path as SysPath

try:  # pragma: no cover - FreeCAD injects this when running internally
    import FreeCAD  # type: ignore
except ImportError:  # pragma: no cover
    FreeCAD = None  # noqa: N816 (FreeCAD is imported for side effects)


def _ensure_waam_parameter_group() -> None:
    """Ensure a WAAM parameter group exists with basic metadata."""
    if FreeCAD is None:
        return
    try:
        modules_root = FreeCAD.ParamGet("System parameter:Modules")
        waam_group = modules_root.GetGroup("WAAM")
        waam_group.SetString("HelpIndex", "")
        waam_group.SetString("WorkBenchName", "WAAM")
        waam_group.SetString("WorkBenchModule", "WaamWorkbench")

        user_mod_parent = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod")
        user_mod_parent.GetGroup("WAAM")
    except Exception:
        # Keep startup resilient; this setup is best-effort.
        pass


def _find_package_name(
    module_dir: SysPath, preferred: tuple[str, ...] = ("Waam_tech", "Waam")
) -> str:
    """Return the package folder name that hosts the workbench code."""
    candidates = []
    for child in module_dir.iterdir():
        if child.is_dir() and (child / "__init__.py").exists() and (child / "wb.py").exists():
            candidates.append(child.name)

    if not candidates:
        return preferred[0]

    for name in preferred:
        for candidate in candidates:
            if candidate.lower() == name.lower():
                return candidate

    return candidates[0]


_MODULE_DIR = SysPath(
    inspect.getfile(inspect.currentframe())
).resolve().parent

_PARENT_DIR = _MODULE_DIR.parent
for path in (_PARENT_DIR, _MODULE_DIR):
    # Parent directory is added so `import WAAM` works even when FreeCAD only
    # prepends the workbench folder itself to sys.path.
    if (path_str := str(path)) not in sys.path:
        sys.path.insert(0, path_str)

_PACKAGE_NAME = _find_package_name(_MODULE_DIR)
_PACKAGE_DIR = _MODULE_DIR / _PACKAGE_NAME
if _PACKAGE_DIR.is_dir() and (package_path := str(_PACKAGE_DIR)) not in sys.path:
    sys.path.insert(0, package_path)

_ensure_waam_parameter_group()
