from __future__ import annotations

from .engineblock import EngineBlockExperimentalPlanner
from .layer_by_layer_scaffold import LayerByLayerExperimentalPlanner
from .pieza_upna_geom import PiezaUpnaGeometryExperimentalPlanner
from .studycadcam_78 import StudyCadCam78ExperimentalPlanner


_PLANNERS = {
    "layer_by_layer": LayerByLayerExperimentalPlanner(),
    "engineblock": EngineBlockExperimentalPlanner(),
    "pieza_upna": PiezaUpnaGeometryExperimentalPlanner(),
    "studycadcam_78": StudyCadCam78ExperimentalPlanner(),
}


def get_experimental_planner(key: str):
    return _PLANNERS.get(key)


def select_experimental_planner(*, fused_operations_path=None, features_path=None):
    """
    Auto-select the generic layer-by-layer experimental scaffold.

    Important rule:
    - auto mode should not silently reuse old part-specific experimental scripts
      for a new part.
    - legacy planners remain available only by explicit key.
    """
    return _PLANNERS.get("layer_by_layer")


def list_experimental_planners():
    return list(_PLANNERS.values())
