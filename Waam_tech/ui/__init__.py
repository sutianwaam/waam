"""UI helpers for the WAAM workbench.

Keep this package initializer lightweight.

Importing GUI-heavy modules here eagerly can create circular imports when core
modules only need a small helper such as ``wcs_marker``.
"""

from __future__ import annotations

import importlib

from .wcs_marker import ensure_tool_wcs_marker, get_stored_wcs, get_wcs_label, store_wcs

__all__ = [
    "commands",
    "ensure_tool_wcs_marker",
    "get_stored_wcs",
    "get_wcs_label",
    "show_llm_dialog",
    "show_wcs_dialog",
    "store_wcs",
]


def __getattr__(name: str):
    if name == "commands":
        return importlib.import_module(f"{__name__}.commands")
    if name == "show_llm_dialog":
        return importlib.import_module(f"{__name__}.ui_llm").show_llm_dialog
    if name == "show_wcs_dialog":
        return importlib.import_module(f"{__name__}.ui_wcs").show_wcs_dialog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
