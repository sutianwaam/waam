from __future__ import annotations

from Waam_tech.nc.support_trim import (
    build_support_index,
    segments_from_polyline,
    support_radius_from_bead,
    trim_polyline_to_support,
)


def test_support_radius_from_bead_defaults_to_half_bead_width():
    assert support_radius_from_bead(6.0) == 3.0


def test_support_radius_from_bead_respects_overlap_ratio():
    radius = support_radius_from_bead(6.0, min_overlap_ratio=0.5)
    assert radius == min(3.0, 6.0 * 0.5)


def test_support_radius_from_bead_respects_overhang_angle():
    radius = support_radius_from_bead(
        6.0, max_overhang_angle_deg=45.0, layer_height=1.5
    )
    assert radius <= 1.5 * 1.000001  # tan(45deg) == 1.0


def test_segments_from_polyline_skips_zero_length_segments():
    segments = segments_from_polyline([(0.0, 0.0), (0.0, 0.0), (1.0, 0.0)])
    assert segments == [((0.0, 0.0), (1.0, 0.0))]


def test_trim_polyline_to_support_keeps_supported_region_only():
    support_segments = [((0.0, 0.0), (5.0, 0.0))]
    index = build_support_index(support_segments, radius=1.0)
    polyline = [(0.0, 0.0), (10.0, 0.0)]
    trimmed = trim_polyline_to_support(polyline, index, radius=1.0, sample_step=0.5)
    assert trimmed
    for piece in trimmed:
        assert all(x <= 6.0 for x, _y in piece)


def test_trim_polyline_to_support_returns_whole_line_without_index():
    polyline = [(0.0, 0.0), (10.0, 0.0)]
    trimmed = trim_polyline_to_support(polyline, None, radius=1.0)
    assert trimmed == [polyline]
