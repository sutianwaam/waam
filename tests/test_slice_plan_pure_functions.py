from __future__ import annotations

import pytest

from _fc_import_shims import load_slice_plan


sp = load_slice_plan()


def _rect(x0: float, y0: float, x1: float, y1: float, *, clockwise: bool = False):
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return list(reversed(points)) if clockwise else points


def test_auto_scale_driver_value_selects_configured_bbox_measure():
    bbox_dims = {
        "height_mm": 4.0,
        "max_dimension_mm": 6.0,
        "xy_diagonal_mm": 7.0,
        "xyz_diagonal_mm": 8.0,
        "max_span_mm": 9.0,
    }

    assert sp._auto_scale_driver_value("height", bbox_dims) == ("height", 4.0)
    assert sp._auto_scale_driver_value("max_dimension", bbox_dims) == ("max_dimension", 6.0)
    assert sp._auto_scale_driver_value("xy_diagonal", bbox_dims) == ("xy_diagonal", 7.0)
    assert sp._auto_scale_driver_value("xyz_diagonal", bbox_dims) == ("xyz_diagonal", 8.0)
    assert sp._auto_scale_driver_value("unknown", bbox_dims) == ("max_span", 9.0)


def test_apply_auto_scale_mutates_deposition_and_path_ranges():
    config = {
        "process": {
            "deposition": {"bead_width_mm": 1.0, "layer_height_mm": 1.0},
            "path": {"overlap_pct": 0.0},
            "auto_scale": {
                "enabled": True,
                "driver": "max_span",
                "span_min_mm": 0.0,
                "span_max_mm": 10.0,
                "deposition": {
                    "ranges": {
                        "bead_width_mm": {"at_min_span": 2.0, "at_max_span": 6.0},
                        "layer_height_mm": {"at_min_span": 1.0, "at_max_span": 3.0},
                    }
                },
                "path": {
                    "ranges": {
                        "overlap_pct": {"at_min_span": 20.0, "at_max_span": 60.0},
                    }
                },
            },
        }
    }

    report = sp._apply_auto_scale(
        config,
        bbox={
            "xmin": 0.0,
            "xmax": 5.0,
            "ymin": 0.0,
            "ymax": 2.0,
            "zmin": 0.0,
            "zmax": 1.0,
        },
    )

    assert report["enabled"] is True
    assert report["applied"] is True
    assert report["blend_factor"] == pytest.approx(0.5)
    assert config["process"]["deposition"]["bead_width_mm"] == pytest.approx(4.0)
    assert config["process"]["deposition"]["layer_height_mm"] == pytest.approx(2.0)
    assert config["process"]["path"]["overlap_pct"] == pytest.approx(40.0)


def test_apply_auto_scale_disabled_reports_without_mutation():
    config = {
        "process": {
            "deposition": {"bead_width_mm": 6.0},
            "auto_scale": {"enabled": False, "span_min_mm": 0.0, "span_max_mm": 10.0},
        }
    }

    report = sp._apply_auto_scale(config, bbox={"xmin": 0.0, "xmax": 10.0})

    assert report["enabled"] is False
    assert report["applied"] is False
    assert config["process"]["deposition"]["bead_width_mm"] == 6.0


def test_rotate_closed_loop_to_target_starts_near_requested_point():
    rotated = sp._rotate_closed_loop_to_target(_rect(0.0, 0.0, 2.0, 2.0), (1.9, 2.1))

    assert rotated[0] == (2.0, 2.0)
    assert rotated[-1] == rotated[0]


def test_hatch_segments_from_polygon_generates_alternating_scanlines():
    segments = sp._hatch_segments_from_polygon(_rect(0.0, 0.0, 4.0, 4.0), spacing=2.0, angle_deg=0.0)

    assert segments == [
        ((0.0, 1.0), (4.0, 1.0)),
        ((4.0, 3.0), (0.0, 3.0)),
    ]


def test_group_regions_assigns_inner_contour_to_smallest_containing_outer():
    outer = sp.Contour(points=_rect(0.0, 0.0, 10.0, 10.0), is_outer=True)
    sibling = sp.Contour(points=_rect(20.0, 20.0, 30.0, 30.0), is_outer=True)
    hole = sp.Contour(points=_rect(2.0, 2.0, 4.0, 4.0, clockwise=True), is_outer=False)

    regions = sp._group_regions([sibling, hole, outer])

    assert len(regions) == 2
    assert regions[0]["outer"] is sibling
    assert regions[0]["holes"] == []
    assert regions[1]["outer"] is outer
    assert regions[1]["holes"] == [hole]


def test_append_hatch_snake_paths_inserts_non_deposition_links_between_gaps():
    paths = {}
    path_ids = []

    next_counter, last_end = sp._append_hatch_snake_paths(
        paths,
        path_ids,
        [((0.0, 0.0), (1.0, 0.0)), ((2.0, 0.0), (3.0, 0.0))],
        path_counter=7,
        region_id="L0_R0",
        region_mode="hatch_only",
    )

    assert next_counter == 10
    assert path_ids == ["P7", "P8", "P9"]
    assert [paths[path_id]["role_hint"] for path_id in path_ids] == ["INFILL", "LINK", "INFILL"]
    assert paths["P8"]["planner_tags"]["skip_deposition"] is True
    assert last_end == (3.0, 0.0)


def test_structured_toolpath_emits_layers_paths_and_region_metadata():
    outer = sp.Contour(points=_rect(0.0, 0.0, 10.0, 10.0), is_outer=True)
    hole = sp.Contour(points=_rect(2.0, 2.0, 4.0, 4.0, clockwise=True), is_outer=False)
    layer = sp.LayerSlice(index=0, z=1.5, contours=[outer, hole])

    toolpath = sp._structured_toolpath(
        [layer],
        bead_width_mm=1.0,
        overlap_pct=0.0,
        start_point_wcs=(9.9, 10.1, 0.0),
        deposition_pattern="contour_hatch",
    )

    assert toolpath["frames"] == {"wcs_frame": {"units": "mm"}}
    assert toolpath["layers"][0]["z"] == 1.5
    assert all(path_id in toolpath["paths"] for path_id in toolpath["layers"][0]["path_ids"])
    role_hints = [path["role_hint"] for path in toolpath["paths"].values()]
    assert "OUTER" in role_hints
    assert "INNER" in role_hints
    assert "INFILL" in role_hints
    assert toolpath["paths"]["P0"]["points_xyz"][0] == [10.0, 10.0, 0.0]
    assert toolpath["paths"]["P0"]["planner_tags"]["region_id"] == "L0_R0"
