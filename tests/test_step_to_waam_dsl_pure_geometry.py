from __future__ import annotations

from _fc_import_shims import load_step_to_waam_dsl


dsl = load_step_to_waam_dsl()


def _rect(x0: float, y0: float, x1: float, y1: float, *, clockwise: bool = False):
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return list(reversed(points)) if clockwise else points


def test_clean_loop_removes_near_duplicate_points_and_closes():
    points = [(0.0, 0.0), (0.0000001, 0.0000001), (1.0, 0.0), (1.0, 1.0)]

    assert dsl._clean_loop(points) == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]


def test_bbox_and_centroid_helpers_ignore_closing_duplicate():
    loop = _rect(-1.0, 2.0, 3.0, 6.0)

    assert dsl._bbox_of_points(loop) == (-1.0, 2.0, 3.0, 6.0)
    assert dsl._bbox_contains((-1.0, 2.0, 3.0, 6.0), (3.25, 3.0), pad=0.5)
    assert not dsl._bbox_contains((-1.0, 2.0, 3.0, 6.0), (3.75, 3.0), pad=0.5)
    assert dsl._centroid_point(loop) == (1.0, 4.0)


def test_point_in_polygon_handles_convex_and_concave_regions():
    square = _rect(0.0, 0.0, 4.0, 4.0)
    concave = [(0.0, 0.0), (3.0, 0.0), (3.0, 1.0), (1.0, 1.0), (1.0, 3.0), (0.0, 3.0)]

    assert dsl._point_in_polygon((2.0, 2.0), square)
    assert not dsl._point_in_polygon((5.0, 2.0), square)
    assert dsl._point_in_polygon((0.5, 2.0), concave)
    assert not dsl._point_in_polygon((2.0, 2.0), concave)


def test_classify_contours_by_depth_marks_holes_and_islands():
    contours = [
        dsl.Contour(points=_rect(4.0, 4.0, 6.0, 6.0), is_outer=False),
        dsl.Contour(points=_rect(0.0, 0.0, 10.0, 10.0), is_outer=False),
        dsl.Contour(points=_rect(2.0, 2.0, 8.0, 8.0), is_outer=True),
    ]

    classified = dsl._classify_contours_by_depth(contours)

    assert [round(abs(contour.area), 3) for contour in classified] == [100.0, 36.0, 4.0]
    assert [contour.is_outer for contour in classified] == [True, False, True]


def test_classify_contours_by_depth_preserves_disjoint_outer_siblings():
    contours = [
        dsl.Contour(points=_rect(0.0, 0.0, 2.0, 2.0), is_outer=False),
        dsl.Contour(points=_rect(4.0, 0.0, 6.0, 2.0), is_outer=False),
    ]

    classified = dsl._classify_contours_by_depth(contours)

    assert [contour.is_outer for contour in classified] == [True, True]


def test_classify_contours_by_depth_normalizes_expected_winding():
    contours = [
        dsl.Contour(points=_rect(0.0, 0.0, 5.0, 5.0, clockwise=True), is_outer=False),
        dsl.Contour(points=_rect(1.0, 1.0, 4.0, 4.0), is_outer=True),
    ]

    classified = dsl._classify_contours_by_depth(contours)

    assert classified[0].is_outer is True
    assert classified[0].area > 0.0
    assert classified[1].is_outer is False
    assert classified[1].area < 0.0
