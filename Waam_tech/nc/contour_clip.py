"""Compatibility facade for WAAM contour clipping helpers."""

from __future__ import annotations

from typing import Sequence

from ..core.geometry2d import (
    Point2D as Point,
    Segment2D as Segment,
    clip_paths_to_contours,
    clip_segment_to_polygon,
    point_xy_in_polygon,
)


def point_in_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Return True when the XY point lies inside ``polygon``."""
    return point_xy_in_polygon(x, y, polygon)


__all__ = [
    "Point",
    "Segment",
    "clip_paths_to_contours",
    "clip_segment_to_polygon",
    "point_in_polygon",
]
