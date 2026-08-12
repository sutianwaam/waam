"""Shared Path Job discovery helpers.

FreeCAD's Path Job object has changed shape across versions, so WAAM keeps
the detection heuristic in one place. Callers should use this module instead
of checking ``TypeId`` directly.
"""

from __future__ import annotations

from typing import Any, Iterable


def is_path_job(obj: Any) -> bool:
    """Return True when ``obj`` looks like a FreeCAD Path Job."""

    proxy = getattr(obj, "Proxy", None)
    proxy_type = getattr(proxy, "Type", "")
    if isinstance(proxy_type, str) and proxy_type.lower() == "pathjob":
        return True

    proxy_cls = getattr(proxy, "__class__", None)
    proxy_name = getattr(proxy_cls, "__name__", "")
    if isinstance(proxy_name, str) and "pathjob" in proxy_name.lower():
        return True

    module_name = getattr(proxy_cls, "__module__", "")
    if isinstance(module_name, str) and "PathScripts.PathJob" in module_name:
        return True

    # Older WAAM UI code used TypeId.startswith("Path::Feature"), but that is
    # too broad for individual operations. Treat it as supporting evidence
    # only when the object exposes Path Job-specific structure.
    type_id = getattr(obj, "TypeId", "")
    if isinstance(type_id, str) and type_id.startswith("Path::Feature"):
        if hasattr(obj, "SetupSheet") or hasattr(obj, "Stock") or hasattr(obj, "Operations"):
            return True

    return False


def iter_path_jobs(doc: Any) -> Iterable[Any]:
    """Yield Path Job objects from a FreeCAD document."""

    for obj in getattr(doc, "Objects", []):
        if is_path_job(obj):
            yield obj


def first_path_job(doc: Any) -> Any | None:
    """Return the first Path Job in ``doc`` or None."""

    return next(iter_path_jobs(doc), None)


__all__ = ["first_path_job", "is_path_job", "iter_path_jobs"]
