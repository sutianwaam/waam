"""Deterministic 2D geometry helpers shared by WAAM planners and NC tools."""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

Point2D = Tuple[float, float]
Segment2D = Tuple[Point2D, Point2D]

POINT_TOL = 1e-6


def point2d(point: Sequence[float]) -> Point2D:
    """Return the XY coordinates of a sequence as floats."""
    return (float(point[0]), float(point[1]))


def distance_sq(a: Sequence[float], b: Sequence[float]) -> float:
    ax, ay = point2d(a)
    bx, by = point2d(b)
    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy


def points_close(a: Sequence[float], b: Sequence[float], tol: float = POINT_TOL) -> bool:
    return distance_sq(a, b) <= (float(tol) * float(tol))


def ensure_closed_loop(
    points: Sequence[Sequence[float]],
    *,
    tol: float | None = None,
) -> List[Point2D]:
    """Return float XY points with the first point repeated at the end."""
    pts = [point2d(point) for point in points]
    if not pts:
        return []
    if tol is None:
        closed = pts[0] == pts[-1]
    else:
        closed = points_close(pts[0], pts[-1], tol=tol)
    if not closed:
        pts.append(pts[0])
    return pts


def loop_vertices(
    points: Sequence[Sequence[float]],
    *,
    tol: float | None = None,
) -> List[Point2D]:
    """Return loop vertices without the closing duplicate."""
    pts = ensure_closed_loop(points, tol=tol)
    if len(pts) >= 2:
        if tol is None:
            closed = pts[0] == pts[-1]
        else:
            closed = points_close(pts[0], pts[-1], tol=tol)
        if closed:
            return pts[:-1]
    return pts


def clean_loop(points: Sequence[Sequence[float]], tol: float = POINT_TOL) -> List[Point2D]:
    """Remove near-duplicate consecutive points and close the loop."""
    if not points:
        return []
    tol2 = float(tol) * float(tol)
    cleaned: List[Point2D] = []
    for point in points:
        pt = point2d(point)
        if not cleaned or distance_sq(cleaned[-1], pt) > tol2:
            cleaned.append(pt)
    if cleaned and distance_sq(cleaned[0], cleaned[-1]) > tol2:
        cleaned.append(cleaned[0])
    return cleaned


def bbox_of_points(points: Sequence[Sequence[float]]) -> Tuple[float, float, float, float]:
    pts = [point2d(point) for point in points]
    xs = [point[0] for point in pts]
    ys = [point[1] for point in pts]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_of_loop(points: Sequence[Sequence[float]]) -> Tuple[float, float, float, float]:
    return bbox_of_points(loop_vertices(points))


def bbox_contains(
    bbox_xy: Tuple[float, float, float, float],
    point: Sequence[float],
    pad: float = 0.0,
) -> bool:
    xmin, ymin, xmax, ymax = bbox_xy
    x, y = point2d(point)
    return (xmin - pad) <= x <= (xmax + pad) and (ymin - pad) <= y <= (ymax + pad)


def centroid_point(points: Sequence[Sequence[float]]) -> Point2D:
    """Return the arithmetic centroid, ignoring one exact closing duplicate."""
    if not points:
        return (0.0, 0.0)
    pts = [point2d(point) for point in points]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    count = max(1, len(pts))
    return (sum(x for x, _y in pts) / count, sum(y for _x, y in pts) / count)


def loop_centroid(points: Sequence[Sequence[float]]) -> Point2D:
    """Return the arithmetic centroid of loop vertices."""
    pts = loop_vertices(points)
    if not pts:
        return (0.0, 0.0)
    return (sum(x for x, _y in pts) / len(pts), sum(y for _x, y in pts) / len(pts))


def polygon_area(points: Sequence[Sequence[float]]) -> float:
    """Return signed polygon area."""
    pts = [point2d(point) for point in points]
    n = len(pts)
    if n < 3:
        return 0.0

    area = 0.0
    for idx in range(n - 1):
        x1, y1 = pts[idx]
        x2, y2 = pts[idx + 1]
        area += x1 * y2 - x2 * y1

    x1, y1 = pts[-1]
    x2, y2 = pts[0]
    area += x1 * y2 - x2 * y1
    return 0.5 * area


def point_in_polygon(point: Sequence[float], polygon: Sequence[Sequence[float]]) -> bool:
    """Return True when ``point`` lies inside ``polygon`` by the even-odd rule."""
    x, y = point2d(point)
    pts = [point2d(poly_point) for poly_point in polygon]
    inside = False
    n = len(pts)
    if n < 3:
        return False

    for idx in range(n):
        x1, y1 = pts[idx]
        x2, y2 = pts[(idx + 1) % n]
        intersects = (y1 > y) != (y2 > y)
        if intersects:
            x_at_y = x1 + (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-16)
            if x_at_y > x:
                inside = not inside
    return inside


def point_xy_in_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Compatibility wrapper for callers that pass x/y separately."""
    return point_in_polygon((x, y), polygon)


def intersect_segments_t(
    p1: Sequence[float],
    p2: Sequence[float],
    p3: Sequence[float],
    p4: Sequence[float],
) -> float | None:
    """
    Return the interpolation parameter on p1-p2 where two segments intersect.

    The return value is clamped to [0.0, 1.0]. ``None`` means there is no
    non-parallel segment intersection.
    """
    x1, y1 = point2d(p1)
    x2, y2 = point2d(p2)
    x3, y3 = point2d(p3)
    x4, y4 = point2d(p4)

    den = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)
    if abs(den) < 1e-9:
        return None

    ua = ((x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)) / den
    ub = ((x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)) / den
    if 0.0 - POINT_TOL <= ua <= 1.0 + POINT_TOL and 0.0 - POINT_TOL <= ub <= 1.0 + POINT_TOL:
        return max(0.0, min(1.0, ua))
    return None


def _intersections_with_polygon(
    segment: Segment2D,
    polygon: Sequence[Sequence[float]],
) -> List[float]:
    values: List[float] = []
    pts = [point2d(point) for point in polygon]
    for idx in range(len(pts)):
        t = intersect_segments_t(segment[0], segment[1], pts[idx], pts[(idx + 1) % len(pts)])
        if t is not None:
            values.append(t)
    return values


def clip_segment_to_polygon(
    segment: Segment2D,
    outer_polygon: Sequence[Sequence[float]] | None,
    hole_polygons: Sequence[Sequence[Sequence[float]]],
) -> List[Segment2D]:
    """Clip one segment inside an outer polygon and outside any holes."""
    p1 = point2d(segment[0])
    p2 = point2d(segment[1])
    segment_len_sq = distance_sq(p1, p2)
    if segment_len_sq < 1e-9:
        return [(p1, p2)]

    t_set = {0.0, 1.0}
    if outer_polygon:
        t_set.update(_intersections_with_polygon((p1, p2), outer_polygon))
    for hole in hole_polygons:
        t_set.update(_intersections_with_polygon((p1, p2), hole))

    t_list = sorted(t_set)
    valid_segments: List[Segment2D] = []
    for idx in range(len(t_list) - 1):
        t_start = t_list[idx]
        t_end = t_list[idx + 1]
        if t_end - t_start < POINT_TOL:
            continue

        t_mid = 0.5 * (t_start + t_end)
        mid_x = p1[0] + t_mid * (p2[0] - p1[0])
        mid_y = p1[1] + t_mid * (p2[1] - p1[1])

        is_valid = True
        if outer_polygon and not point_in_polygon((mid_x, mid_y), outer_polygon):
            is_valid = False
        if is_valid:
            for hole in hole_polygons:
                if point_in_polygon((mid_x, mid_y), hole):
                    is_valid = False
                    break

        if is_valid:
            start = (
                p1[0] + t_start * (p2[0] - p1[0]),
                p1[1] + t_start * (p2[1] - p1[1]),
            )
            end = (
                p1[0] + t_end * (p2[0] - p1[0]),
                p1[1] + t_end * (p2[1] - p1[1]),
            )
            valid_segments.append((start, end))

    return valid_segments


def clip_paths_to_contours(
    paths: Sequence[Segment2D],
    outer_contour: Sequence[Sequence[float]] | None,
    inner_contours: Sequence[Sequence[Sequence[float]]],
) -> List[Segment2D]:
    """Clip hatch/path segments to an outer contour while excluding holes."""
    if not paths:
        return []
    if not outer_contour and not inner_contours:
        return list(paths)

    clipped_paths: List[Segment2D] = []
    for segment in paths:
        clipped_paths.extend(clip_segment_to_polygon(segment, outer_contour, inner_contours))
    return clipped_paths


def rotate_point(
    x: float,
    y: float,
    *,
    angle_rad: float,
    origin: Point2D,
    inverse: bool = False,
) -> Point2D:
    if inverse:
        angle_rad = -angle_rad
    ox, oy = origin
    dx = float(x) - ox
    dy = float(y) - oy
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return (ox + (dx * cos_a - dy * sin_a), oy + (dx * sin_a + dy * cos_a))


def scanline_segments_at_y(
    rotated_polygon: Sequence[Point2D],
    *,
    y_value: float,
    angle_rad: float,
    origin: Point2D,
    reverse: bool = False,
) -> List[Segment2D]:
    x_hits: List[float] = []
    for (x1, y1), (x2, y2) in zip(rotated_polygon, rotated_polygon[1:]):
        if abs(y1 - y2) < 1e-9:
            continue
        if (y1 <= y_value < y2) or (y2 <= y_value < y1):
            t = (y_value - y1) / (y2 - y1)
            x_hits.append(x1 + t * (x2 - x1))
    x_hits.sort()

    segments: List[Segment2D] = []
    for idx in range(0, len(x_hits) - 1, 2):
        start = rotate_point(x_hits[idx], y_value, angle_rad=angle_rad, origin=origin, inverse=True)
        end = rotate_point(x_hits[idx + 1], y_value, angle_rad=angle_rad, origin=origin, inverse=True)
        segments.append((start, end))
    if reverse:
        segments = [(end, start) for start, end in reversed(segments)]
    return segments


def hatch_segments_from_polygon(
    polygon: Sequence[Sequence[float]],
    *,
    spacing: float,
    angle_deg: float,
    ensure_center_pass: bool = False,
) -> List[Segment2D]:
    pts = ensure_closed_loop(polygon)
    if len(pts) < 4 or spacing <= 0.0:
        return []

    xs = [point[0] for point in pts]
    ys = [point[1] for point in pts]
    origin = ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5)
    angle_rad = math.radians(float(angle_deg) % 180.0)

    rotated = [rotate_point(x, y, angle_rad=angle_rad, origin=origin) for x, y in pts]
    y_vals = [point[1] for point in rotated]
    y = min(y_vals) + spacing * 0.5
    y_max = max(y_vals)

    segments: List[Segment2D] = []
    reverse = False
    while y <= y_max + 1e-6:
        segments.extend(
            scanline_segments_at_y(
                rotated,
                y_value=y,
                angle_rad=angle_rad,
                origin=origin,
                reverse=reverse,
            )
        )
        reverse = not reverse
        y += spacing

    if not segments and ensure_center_pass:
        center_y = (min(y_vals) + max(y_vals)) * 0.5
        segments.extend(
            scanline_segments_at_y(
                rotated,
                y_value=center_y,
                angle_rad=angle_rad,
                origin=origin,
            )
        )
    return segments


__all__ = [
    "POINT_TOL",
    "Point2D",
    "Segment2D",
    "bbox_contains",
    "bbox_of_loop",
    "bbox_of_points",
    "centroid_point",
    "clean_loop",
    "clip_paths_to_contours",
    "clip_segment_to_polygon",
    "distance_sq",
    "ensure_closed_loop",
    "hatch_segments_from_polygon",
    "intersect_segments_t",
    "loop_centroid",
    "loop_vertices",
    "point2d",
    "point_in_polygon",
    "point_xy_in_polygon",
    "points_close",
    "polygon_area",
    "rotate_point",
    "scanline_segments_at_y",
]
