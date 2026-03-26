from __future__ import annotations

from .layer_by_layer_scaffold import LayerByLayerExperimentalPlanner
from .legacy.engineblock import EngineBlockExperimentalPlanner
from .legacy.pieza_upna_geom import PiezaUpnaGeometryExperimentalPlanner
from .legacy.studycadcam_78 import StudyCadCam78ExperimentalPlanner
from .runtime.loader import RuntimeActiveExperimentalPlanner


_PLANNERS = {
    "runtime_active": RuntimeActiveExperimentalPlanner(),
    "layer_by_layer": LayerByLayerExperimentalPlanner(),
    "legacy_engineblock": EngineBlockExperimentalPlanner(),
    "legacy_pieza_upna": PiezaUpnaGeometryExperimentalPlanner(),
    "legacy_studycadcam_78": StudyCadCam78ExperimentalPlanner(),
}
_PLANNER_ALIASES = {
    "engineblock": "legacy_engineblock",
    "pieza_upna": "legacy_pieza_upna",
    "studycadcam_78": "legacy_studycadcam_78",
}


def get_experimental_planner(key: str):
    actual_key = _PLANNER_ALIASES.get(key, key)
    return _PLANNERS.get(actual_key)


def select_experimental_planner(*, fused_operations_path=None, features_path=None):
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
