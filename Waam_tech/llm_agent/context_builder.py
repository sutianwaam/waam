from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import FreeCAD as App  # type: ignore

try:  # pragma: no cover - GUI may be unavailable in headless runs
    import FreeCADGui as Gui  # type: ignore
except ImportError:  # pragma: no cover
    Gui = None

from ..core.paths import get_features_path, get_operations_path, get_output_dir

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "standard_waam.json"


def _output_dir() -> Path:
    return get_output_dir(create=True)


def _waam_plan_path() -> Path:
    return _output_dir() / "waam_plan.json"


def _gather_job_info(doc) -> Dict[str, Any]:
    job = next(
        (
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "").startswith("Path::Feature")
        ),
        None,
    )
    if job is None:
        return {}

    ops_summary: List[Dict[str, Any]] = []
    path_attr = getattr(job, "Path", None)
    if path_attr and getattr(path_attr, "Operations", None):
        for op in path_attr.Operations:
            tool = getattr(op, "ToolController", None)
            ops_summary.append(
                {
                    "label": getattr(op, "Label", getattr(op, "Name", "Operation")),
                    "type": getattr(op, "Proxy", op).__class__.__name__,
                    "tool": getattr(tool, "Label", getattr(tool, "Name", None)),
                }
            )

    return {
        "job_label": getattr(job, "Label", job.Name),
        "operations": ops_summary,
    }


def _vector_to_list(vec) -> List[float]:
    return [float(vec.x), float(vec.y), float(vec.z)]


def _gather_selection() -> List[Dict[str, Any]]:
    if Gui is None:
        return []

    selections = []
    for sel in Gui.Selection.getSelectionEx():
        picked_points = []
        for point in getattr(sel, "PickedPoints", []) or []:
            try:
                picked_points.append(_vector_to_list(point))
            except AttributeError:
                continue

        selections.append(
            {
                "object": sel.ObjectName,
                "label": getattr(sel.Object, "Label", sel.ObjectName),
                "sub_elements": list(getattr(sel, "SubElementNames", []) or []),
                "picked_points": picked_points,
            }
        )

    return selections


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def build_context(include_config: bool = True) -> Dict[str, Any]:
    doc = App.ActiveDocument
    if doc is None:
        return {"error": "No active document open in FreeCAD."}

    solids = [
        {"name": obj.Name, "label": getattr(obj, "Label", obj.Name)}
        for obj in doc.Objects
        if hasattr(obj, "Shape") and obj.Shape and obj.Shape.Solids
    ]

    context: Dict[str, Any] = {
        "document": {"name": doc.Name, "label": getattr(doc, "Label", doc.Name)},
        "solid_count": len(solids),
        "solids": solids,
        "job": _gather_job_info(doc),
        "selection": _gather_selection(),
    }

    if include_config:
        config_text = _read_text(_CONFIG_PATH)
        if config_text:
            context["config_json"] = config_text

    plan_path = _waam_plan_path()
    if plan_path.is_file():
        context["waam_plan_path"] = str(plan_path)

    features_path = get_features_path(prefer_existing=True)
    if features_path.is_file():
        context["features_path"] = str(features_path)

    ops_path = get_operations_path(prefer_existing=True)
    if ops_path.is_file():
        context["operations_path"] = str(ops_path)

    return context


def context_as_text(context: Dict[str, Any]) -> str:
    serializable = json.dumps(context, indent=2)
    return f"FreeCAD WAAM Context:\n{serializable}"
