from __future__ import annotations

import pytest

from _fc_import_shims import load_slice_plan, load_step_to_waam_dsl
from Waam_tech.core import geometry2d as g2
from Waam_tech.nc import contour_clip


def _rect(x0: float, y0: float, x1: float, y1: float):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def test_loop_area_bbox_centroid_and_cleaning_share_one_implementation():
    noisy_loop = [(0.0, 0.0), (0.0000001, 0.0000001), (1.0, 0.0), (1.0, 1.0)]
    square = _rect(-1.0, 2.0, 3.0, 6.0)

    assert g2.clean_loop(noisy_loop) == [
        (0.0, 0.0),
        (1.0, 0.0),
        (1.0, 1.0),
        (0.0, 0.0),
    ]
    assert g2.polygon_area(square) == pytest.approx(16.0)
    assert g2.bbox_of_points(square) == (-1.0, 2.0, 3.0, 6.0)
    assert g2.bbox_of_loop(square) == (-1.0, 2.0, 3.0, 6.0)
    assert g2.bbox_contains((-1.0, 2.0, 3.0, 6.0), (3.25, 3.0), pad=0.5)
    assert not g2.bbox_contains((-1.0, 2.0, 3.0, 6.0), (3.75, 3.0), pad=0.5)
    assert g2.centroid_point(square) == (1.0, 4.0)
    assert g2.loop_centroid(square) == (1.0, 4.0)


def test_point_in_polygon_wrappers_match_shared_concave_semantics():
    concave = [
        (0.0, 0.0),
        (3.0, 0.0),
        (3.0, 1.0),
        (1.0, 1.0),
        (1.0, 3.0),
        (0.0, 3.0),
    ]

    assert g2.point_in_polygon((0.5, 2.0), concave)
    assert not g2.point_in_polygon((2.0, 2.0), concave)
    assert contour_clip.point_in_polygon(0.5, 2.0, concave) == g2.point_xy_in_polygon(
        0.5, 2.0, concave
    )
    assert contour_clip.point_in_polygon(2.0, 2.0, concave) == g2.point_xy_in_polygon(
        2.0, 2.0, concave
    )


def test_clip_paths_to_contours_excludes_holes_with_stable_order():
    outer = _rect(0.0, 0.0, 10.0, 10.0)
    hole = _rect(4.0, 4.0, 6.0, 6.0)
    paths = [
        ((1.0, 5.0), (9.0, 5.0)),
        ((-2.0, 1.0), (2.0, 1.0)),
    ]

    assert g2.clip_paths_to_contours(paths, outer, [hole]) == pytest.approx(
        [
            ((1.0, 5.0), (4.0, 5.0)),
            ((6.0, 5.0), (9.0, 5.0)),
            ((0.0, 1.0), (2.0, 1.0)),
        ]
    )


def test_legacy_planner_dsl_and_nc_entry_points_delegate_to_geometry2d():
    dsl = load_step_to_waam_dsl()
    slice_plan = load_slice_plan()

    assert dsl.polygon_area is g2.polygon_area
    assert dsl._clean_loop is g2.clean_loop
    assert dsl._bbox_of_points is g2.bbox_of_points
    assert dsl._bbox_contains is g2.bbox_contains
    assert dsl._centroid_point is g2.centroid_point
    assert dsl._point_in_polygon is g2.point_in_polygon

    assert slice_plan._ensure_closed is g2.ensure_closed_loop
    assert slice_plan._loop_vertices is g2.loop_vertices
    assert slice_plan._bbox_of_loop is g2.bbox_of_loop
    assert slice_plan._centroid is g2.loop_centroid
    assert slice_plan._hatch_segments_from_polygon is g2.hatch_segments_from_polygon
    assert slice_plan._bbox_contains((0.0, 0.0, 1.0, 1.0), (1.0 + 5e-7, 0.5))
    assert (
        slice_plan._bbox_contains((0.0, 0.0, 1.0, 1.0), (1.0 + 5e-7, 0.5), pad=0.0)
        is False
    )

    assert contour_clip.clip_segment_to_polygon is g2.clip_segment_to_polygon
    assert contour_clip.clip_paths_to_contours is g2.clip_paths_to_contours
