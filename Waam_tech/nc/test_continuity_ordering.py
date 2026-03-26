import copy

from Waam_tech.nc.fuse_ops_mesh import (
    _order_layer_paths_continuously,
    _rotate_closed_path_to_target,
)


def _path(points, *, role="OTHER", closed=False, region_id="R0", reversible=True):
    return {
        "id": "P",
        "role_hint": role,
        "frame": "mesh_frame",
        "closed": closed,
        "points_xyz": points,
        "planner_tags": {
            "region_id": region_id,
            "continuity_reversible": reversible,
        },
    }


def _continuity_cfg() -> dict:
    return {
        "enabled": True,
        "rotate_closed_paths": True,
        "allow_reverse_open_paths": True,
        "restart_penalty_mm": 20.0,
        "heading_change_penalty_mm": 4.0,
        "region_switch_penalty_mm": 3.0,
    }


def test_closed_loop_rotates_to_nearest_previous_endpoint() -> None:
    loop = [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 10.0, 0.0], [0.0, 10.0, 0.0], [0.0, 0.0, 0.0]]
    rotated = _rotate_closed_path_to_target(loop, (9.5, 0.5))

    assert rotated[0][:2] == [10.0, 0.0]
    assert rotated[-1][:2] == [10.0, 0.0]


def test_open_segment_reverses_when_reverse_reduces_jump() -> None:
    paths = {
        "P1": _path([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], region_id="R0"),
        "P2": _path([[5.0, 0.0, 0.0], [2.0, 0.0, 0.0]], region_id="R0"),
    }
    layer = {"path_ids": ["P1", "P2"]}

    ordered_ids, summary = _order_layer_paths_continuously(layer, paths, continuity_cfg=_continuity_cfg())

    assert ordered_ids == ["P1", "P2"]
    assert "P2" in summary["reversed_paths"]
    assert paths["P2"]["points_xyz"][0][:2] == [2.0, 0.0]


def test_continuity_ordering_prefers_lower_total_jump() -> None:
    paths = {
        "P1": _path([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], region_id="R0"),
        "P2": _path([[20.0, 0.0, 0.0], [21.0, 0.0, 0.0]], region_id="R1"),
        "P3": _path([[2.0, 0.0, 0.0], [3.0, 0.0, 0.0]], region_id="R0"),
    }
    layer = {"path_ids": ["P1", "P2", "P3"]}

    ordered_ids, _summary = _order_layer_paths_continuously(layer, paths, continuity_cfg=_continuity_cfg())

    assert ordered_ids[:2] == ["P1", "P3"]


def test_continuity_ordering_is_deterministic_for_equal_cost_candidates() -> None:
    paths_a = {
        "P1": _path([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], region_id="R0"),
        "P2": _path([[0.0, 2.0, 0.0], [1.0, 2.0, 0.0]], region_id="R0"),
    }
    paths_b = copy.deepcopy(paths_a)
    layer = {"path_ids": ["P1", "P2"]}

    ordered_a, summary_a = _order_layer_paths_continuously(layer, paths_a, continuity_cfg=_continuity_cfg())
    ordered_b, summary_b = _order_layer_paths_continuously(layer, paths_b, continuity_cfg=_continuity_cfg())

    assert ordered_a == ordered_b == ["P1", "P2"]
    assert summary_a == summary_b
