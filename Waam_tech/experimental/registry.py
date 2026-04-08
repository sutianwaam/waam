from __future__ import annotations

from .runtime.loader import RuntimeActiveExperimentalPlanner


_PLANNERS = {
    "runtime_active": RuntimeActiveExperimentalPlanner(),
}
_PLANNER_ALIASES = {}


def get_experimental_planner(key: str):
    actual_key = _PLANNER_ALIASES.get(key, key)
    return _PLANNERS.get(actual_key)


def select_experimental_planner(*, plan_path=None, features_path=None, fused_operations_path=None):
    """
    Auto-select the active runtime sandbox planner.

    Important rule:
    - auto mode should always route through the active runtime sandbox under
      Waam_tech/experimental/runtime/active/.
    - runtime export is responsible for resetting and archiving the sandbox
      safely when the active part changes.
    - legacy planners remain available only by explicit key.
    """
    return _PLANNERS.get("runtime_active")


def list_experimental_planners():
    seen = set()
    planners = []
    for planner in _PLANNERS.values():
        ident = getattr(planner, "key", id(planner))
        if ident in seen:
            continue
        seen.add(ident)
        planners.append(planner)
    return planners
