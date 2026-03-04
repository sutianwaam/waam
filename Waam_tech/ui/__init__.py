"""UI helpers for the simplified WAAM workbench."""

from __future__ import annotations

from .wcs_marker import ensure_tool_wcs_marker, get_stored_wcs, get_wcs_label, store_wcs

import FreeCAD as App

if App.GuiUp:
    from . import commands
    from .ui_llm import show_llm_dialog

    from .ui_wcs import show_wcs_dialog

    __all__ = [
        "commands",
        "ensure_tool_wcs_marker",
        "get_stored_wcs",
        "get_wcs_label",
        "show_llm_dialog",

        "show_wcs_dialog",
        "store_wcs",
    ]
else:
    # In console mode, expose only non-GUI helpers
    __all__ = [
        "ensure_tool_wcs_marker",
        "get_stored_wcs",
        "get_wcs_label",
        "store_wcs",
    ]
