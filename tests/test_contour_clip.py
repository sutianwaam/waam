from __future__ import annotations

from Waam_tech.nc.contour_clip import clip_segment_to_polygon, point_in_polygon

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_point_in_polygon_inside_and_outside():
    assert point_in_polygon(5.0, 5.0, SQUARE) is True
    assert point_in_polygon(15.0, 5.0, SQUARE) is False


def test_clip_segment_fully_inside_polygon_is_unchanged():
    seg = ((1.0, 5.0), (9.0, 5.0))
    result = clip_segment_to_polygon(seg, SQUARE, [])
    assert len(result) == 1
    (sx, sy), (ex, ey) = result[0]
    assert (sx, sy) == (1.0, 5.0)
    assert (ex, ey) == (9.0, 5.0)


def test_clip_segment_crossing_boundary_is_trimmed():
    seg = ((-5.0, 5.0), (5.0, 5.0))
    result = clip_segment_to_polygon(seg, SQUARE, [])
    assert len(result) == 1
    (sx, _sy), (ex, _ey) = result[0]
    assert sx == 0.0
    assert ex == 5.0


def test_clip_segment_excludes_hole_interior():
    hole = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]
    seg = ((1.0, 5.0), (9.0, 5.0))
    result = clip_segment_to_polygon(seg, SQUARE, [hole])
    # Segment should be split into two pieces around the hole.
    assert len(result) == 2
