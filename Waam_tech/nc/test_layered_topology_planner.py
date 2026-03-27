import copy

from Waam_tech.nc.fuse_ops_mesh import _layered_topology_planner, _parse_llm_paths


def _config() -> dict:
    return {
        "process": {
            "deposition": {
                "bead_width_mm": 2.0,
                "overlap_pct": 0.0,
            },
            "path": {
                "planner_mode": "layered_topology",
                "infill_strategy": "zigzag",
                "angle_deg": 0.0,
                "angle_step_deg": 90.0,
                "topology_sensitive": {
                    "enabled": True,
                    "void_lookahead_layers": 2,
                    "thin_wall_area_ratio_max": 0.45,
                    "suppress_infill_on_transition": True,
                    "thin_wall_detection": {
                        "enabled": True,
                        "use_mesh_signal": True,
                        "sample_step_mm": 1.0,
                        "max_samples_per_region": 96,
                        "single_track_max_width_beads": 1.35,
                        "dual_track_max_width_beads": 2.35,
                    },
                    "hotspots": {
                        "corner_angle_deg": 45.0,
                        "corner_clearance_mm": 1.0,
                        "junction_clearance_mm": 4.5,
                        "min_neck_width_beads": 2.0,
                        "suppress_infill_near_hotspots": True,
                    },
                    "continuity": {
                        "enabled": True,
                        "rotate_closed_paths": True,
                        "allow_reverse_open_paths": True,
                        "restart_penalty_mm": 20.0,
                        "heading_change_penalty_mm": 4.0,
                        "region_switch_penalty_mm": 3.0,
                        "max_chain_gap_mm": 0.8,
                    },
                },
            },
        },
    }


def _closed(points):
    if points[0] == points[-1]:
        return points
    return points + [points[0]]


def _structured_from_paths(paths: dict[str, dict], layers: list[dict]) -> dict:
    return {"paths": paths, "layers": layers}


def _infill_ids(planned: dict, layer_index: int = 0) -> list[str]:
    layer = planned["layers"][layer_index]
    return [pid for pid in layer["path_ids"] if planned["paths"][pid]["role_hint"] == "INFILL"]


def test_single_track_wall_region_disables_infill_and_sets_region_mode() -> None:
    outer = _closed([[0.0, 0.0, 0.0], [20.0, 0.0, 0.0], [20.0, 2.4, 0.0], [0.0, 2.4, 0.0]])
    structured = _structured_from_paths(
        {
            "P_OUTER": {"id": "P_OUTER", "role_hint": "OUTER", "frame": "mesh_frame", "closed": True, "points_xyz": outer},
        },
        [{"i": 0, "z": 0.0, "path_ids": ["P_OUTER"], "frame": "mesh_frame"}],
    )

    planned = _layered_topology_planner(structured, config=_config(), features={"metadata": {}})

    assert _infill_ids(planned) == []
    assert planned["layers"][0]["topology"]["region_modes"] == {"R0": "single_track_wall"}


def test_dual_track_wall_region_keeps_wall_only_mode() -> None:
    outer = _closed([[0.0, 0.0, 0.0], [20.0, 0.0, 0.0], [20.0, 4.0, 0.0], [0.0, 4.0, 0.0]])
    structured = _structured_from_paths(
        {
            "P_OUTER": {"id": "P_OUTER", "role_hint": "OUTER", "frame": "mesh_frame", "closed": True, "points_xyz": outer},
        },
        [{"i": 0, "z": 0.0, "path_ids": ["P_OUTER"], "frame": "mesh_frame"}],
    )

    planned = _layered_topology_planner(structured, config=_config(), features={"metadata": {}})

    assert _infill_ids(planned) == []
    assert planned["layers"][0]["topology"]["region_modes"] == {"R0": "dual_track_wall"}


def test_transition_region_suppresses_infill_near_future_void() -> None:
    outer = _closed([[0.0, 0.0, 0.0], [20.0, 0.0, 0.0], [20.0, 10.0, 0.0], [0.0, 10.0, 0.0]])
    inner = _closed([[7.0, 3.0, 0.0], [13.0, 3.0, 0.0], [13.0, 7.0, 0.0], [7.0, 7.0, 0.0]])
    structured = _structured_from_paths(
        {
            "P_OUTER": {"id": "P_OUTER", "role_hint": "OUTER", "frame": "mesh_frame", "closed": True, "points_xyz": outer},
            "P_INNER": {"id": "P_INNER", "role_hint": "INNER", "frame": "mesh_frame", "closed": True, "points_xyz": inner},
        },
        [
            {"i": 0, "z": 0.0, "path_ids": ["P_OUTER"], "frame": "mesh_frame"},
            {"i": 1, "z": 1.0, "path_ids": ["P_OUTER", "P_INNER"], "frame": "mesh_frame"},
        ],
    )

    planned = _layered_topology_planner(structured, config=_config(), features={"metadata": {}})

    assert _infill_ids(planned, 0) == []
    assert _infill_ids(planned, 1) == []
    assert planned["layers"][0]["topology"]["region_modes"]["R0"] == "transition_region"


def test_stable_hole_topology_does_not_stay_in_transition_mode() -> None:
    outer = _closed([[0.0, 0.0, 0.0], [20.0, 0.0, 0.0], [20.0, 12.0, 0.0], [0.0, 12.0, 0.0]])
    inner = _closed([[7.0, 4.0, 0.0], [13.0, 4.0, 0.0], [13.0, 8.0, 0.0], [7.0, 8.0, 0.0]])
    structured = _structured_from_paths(
        {
            "P_OUTER": {"id": "P_OUTER", "role_hint": "OUTER", "frame": "mesh_frame", "closed": True, "points_xyz": outer},
            "P_INNER": {"id": "P_INNER", "role_hint": "INNER", "frame": "mesh_frame", "closed": True, "points_xyz": inner},
        },
        [
            {"i": 0, "z": 0.0, "path_ids": ["P_OUTER", "P_INNER"], "frame": "mesh_frame"},
            {"i": 1, "z": 1.0, "path_ids": ["P_OUTER", "P_INNER"], "frame": "mesh_frame"},
            {"i": 2, "z": 2.0, "path_ids": ["P_OUTER", "P_INNER"], "frame": "mesh_frame"},
        ],
    )

    planned = _layered_topology_planner(structured, config=_config(), features={"metadata": {}})

    assert planned["layers"][1]["topology"]["region_modes"]["R0"] != "transition_region"
    assert _infill_ids(planned, 1) != []


def test_corner_hotspots_are_reported_in_layer_metadata() -> None:
    outer = _closed([[0.0, 0.0, 0.0], [20.0, 0.0, 0.0], [20.0, 10.0, 0.0], [0.0, 10.0, 0.0]])
    structured = _structured_from_paths(
        {
            "P_OUTER": {"id": "P_OUTER", "role_hint": "OUTER", "frame": "mesh_frame", "closed": True, "points_xyz": outer},
        },
        [{"i": 0, "z": 0.0, "path_ids": ["P_OUTER"], "frame": "mesh_frame"}],
    )

    planned = _layered_topology_planner(structured, config=_config(), features={"metadata": {}})
    hotspots = planned["layers"][0]["topology"]["hotspots"]
    corners = [item for item in hotspots if item["kind"] == "corner"]

    assert len(corners) == 4


def test_branch_junction_hotspot_suppresses_local_infill_segments() -> None:
    outer = _closed([
        [0.0, 0.0, 0.0],
        [30.0, 0.0, 0.0],
        [30.0, 8.0, 0.0],
        [19.0, 8.0, 0.0],
        [19.0, 24.0, 0.0],
        [11.0, 24.0, 0.0],
        [11.0, 8.0, 0.0],
        [0.0, 8.0, 0.0],
    ])
    structured = _structured_from_paths(
        {
            "P_OUTER": {"id": "P_OUTER", "role_hint": "OUTER", "frame": "mesh_frame", "closed": True, "points_xyz": outer},
        },
        [{"i": 0, "z": 0.0, "path_ids": ["P_OUTER"], "frame": "mesh_frame"}],
    )

    cfg_with_suppression = _config()
    cfg_without_suppression = copy.deepcopy(cfg_with_suppression)
    cfg_without_suppression["process"]["path"]["topology_sensitive"]["hotspots"]["suppress_infill_near_hotspots"] = False

    planned_with_suppression = _layered_topology_planner(copy.deepcopy(structured), config=cfg_with_suppression, features={"metadata": {}})
    planned_without_suppression = _layered_topology_planner(copy.deepcopy(structured), config=cfg_without_suppression, features={"metadata": {}})

    hotspots = planned_with_suppression["layers"][0]["topology"]["hotspots"]
    assert any(item["kind"] == "junction" for item in hotspots)
    assert len(_infill_ids(planned_with_suppression)) < len(_infill_ids(planned_without_suppression))


def test_layer_metadata_includes_continuity_summary() -> None:
    outer_a = _closed([[0.0, 0.0, 0.0], [8.0, 0.0, 0.0], [8.0, 6.0, 0.0], [0.0, 6.0, 0.0]])
    outer_b = _closed([[20.0, 0.0, 0.0], [28.0, 0.0, 0.0], [28.0, 6.0, 0.0], [20.0, 6.0, 0.0]])
    structured = _structured_from_paths(
        {
            "P_A": {"id": "P_A", "role_hint": "OUTER", "frame": "mesh_frame", "closed": True, "points_xyz": outer_a},
            "P_B": {"id": "P_B", "role_hint": "OUTER", "frame": "mesh_frame", "closed": True, "points_xyz": outer_b},
        },
        [{"i": 0, "z": 0.0, "path_ids": ["P_B", "P_A"], "frame": "mesh_frame"}],
    )

    planned = _layered_topology_planner(structured, config=_config(), features={"metadata": {}})
    continuity = planned["layers"][0]["topology"]["continuity"]

    assert continuity["ordered_path_ids"]
    assert "estimated_restarts" in continuity
    assert "non_deposition_jump_mm" in continuity


def test_parse_llm_paths_ignores_layer_height_and_normalizes_duplicate_refs() -> None:
    text = """
WAAMPART part
UNITS mm
LAYER_HEIGHT 1.2
BEAD_WIDTH 6
PATHLIB_BEGIN
PATH ID=P0 ROLE_HINT=OUTER
(0,0)(10,0)(10,10)(0,10)(0,0)
END_PATH
PATH ID=P1 ROLE_HINT=OUTER
(2,2)(8,2)(8,8)(2,8)(2,2)
END_PATH
PATHLIB_END
LAYER 2 Z=0
USE_PATH ID=P0 AS=OUTER_CONTOUR
USE_PATH ID=P0 AS=INNER_CONTOUR
USE_PATH ID=P1 AS=INNER_CONTOUR
END_LAYER
END_PART
""".strip()

    structured = _parse_llm_paths(text)

    assert [layer["i"] for layer in structured["layers"]] == [2]
    layer = structured["layers"][0]
    assert len(layer["path_ids"]) == 2

    role_by_source = {
        structured["paths"][path_id]["source_path_id"]: structured["paths"][path_id]["role_hint"]
        for path_id in layer["path_ids"]
    }
    assert role_by_source == {"P0": "OUTER", "P1": "INNER"}


def test_band_fill_region_emits_infill_for_broad_planar_band() -> None:
    outer = _closed([[0.0, 0.0, 0.0], [16.0, 0.0, 0.0], [16.0, 12.0, 0.0], [0.0, 12.0, 0.0]])
    structured = _structured_from_paths(
        {
            "P_OUTER": {"id": "P_OUTER", "role_hint": "OUTER", "frame": "mesh_frame", "closed": True, "points_xyz": outer},
        },
        [{"i": 0, "z": 0.0, "path_ids": ["P_OUTER"], "frame": "mesh_frame"}],
    )

    cfg = _config()
    cfg["process"]["deposition"]["bead_width_mm"] = 6.0
    cfg["process"]["deposition"]["overlap_pct"] = 30.0

    planned = _layered_topology_planner(structured, config=cfg, features={"metadata": {}})

    assert planned["layers"][0]["topology"]["region_modes"] == {"R0": "band_fill"}
    assert _infill_ids(planned) != []
