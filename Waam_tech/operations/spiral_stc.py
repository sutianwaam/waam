"""Spiral spanning tree coverage planner adapted for WAAM infill."""

from __future__ import annotations

import math
import sys
from typing import List, Optional, Sequence, Tuple

Point2D = Tuple[float, float]
GridPoint = Tuple[int, int]


def _ensure_closed_loop(points: Sequence[Sequence[float]]) -> List[Point2D]:
    cleaned: List[Point2D] = []
    for entry in points:
        if len(entry) < 2:
            continue
        try:
            cleaned.append((float(entry[0]), float(entry[1])))
        except Exception:
            continue
    if len(cleaned) < 3:
        return []
    if cleaned[0] != cleaned[-1]:
        cleaned.append(cleaned[0])
    return cleaned


def _point_in_polygon(point: Point2D, polygon: Sequence[Point2D]) -> bool:
    x, y = point
    inside = False
    count = len(polygon)
    if count < 3:
        return False
    for i in range(count):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % count]
        if (y1 > y) != (y2 > y):
            x_int = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1
            if x <= x_int:
                inside = not inside
    return inside


def _grid_from_polygon(
    polygon: Sequence[Point2D],
    *,
    cell_size: float,
    padding_cells: int = 1,
) -> Tuple[List[List[int]], float, float]:
    xs = [pt[0] for pt in polygon]
    ys = [pt[1] for pt in polygon]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if cell_size <= 0.0:
        raise ValueError("cell_size must be > 0.")

    span_x = max(max_x - min_x, 0.0)
    span_y = max(max_y - min_y, 0.0)

    width = max(1, int(math.ceil(span_x / cell_size)) + 2 * padding_cells)
    height = max(1, int(math.ceil(span_y / cell_size)) + 2 * padding_cells)
    if width % 2:
        width += 1
    if height % 2:
        height += 1

    origin_x = min_x - padding_cells * cell_size
    origin_y = min_y - padding_cells * cell_size

    grid: List[List[int]] = [[0 for _ in range(width)] for _ in range(height)]
    for row in range(height):
        y = origin_y + (row + 0.5) * cell_size
        for col in range(width):
            x = origin_x + (col + 0.5) * cell_size
            if _point_in_polygon((x, y), polygon):
                grid[row][col] = 1
    return grid, origin_x, origin_y


class SpiralSpanningTreeCoveragePlanner:
    def __init__(self, occ_map: Sequence[Sequence[int]]):
        height = len(occ_map)
        width = len(occ_map[0]) if height else 0
        if height == 0 or width == 0:
            raise ValueError("Occupancy map is empty.")
        if height % 2 or width % 2:
            raise ValueError("Occupancy map width/height must be even.")
        self.occ_map = occ_map
        self.origin_map_height = height
        self.origin_map_width = width
        self.merged_map_height = height // 2
        self.merged_map_width = width // 2
        self.edge: List[Tuple[GridPoint, GridPoint]] = []
        self.adj: Dict[GridPoint, set[GridPoint]] = {}

    def plan(self, start: Optional[GridPoint] = None) -> List[Tuple[GridPoint, GridPoint]]:
        visit_times = [[0 for _ in range(self.merged_map_width)] for _ in range(self.merged_map_height)]
        start = start or self._find_start(visit_times)
        if start is None:
            return []
        visit_times[start[0]][start[1]] = 1

        route: List[GridPoint] = []
        self._perform_spanning_tree_coverage(start, visit_times, route)

        path: List[Tuple[GridPoint, GridPoint]] = []
        for idx in range(len(route) - 1):
            dp = abs(route[idx][0] - route[idx + 1][0]) + abs(route[idx][1] - route[idx + 1][1])
            if dp == 0:
                if idx == 0:
                    continue
                path.append(self._get_round_trip_path(route[idx - 1], route[idx]))
            elif dp == 1:
                path.append(self._move(route[idx], route[idx + 1]))
            elif dp == 2:
                mid = self._get_intermediate_node(route[idx], route[idx + 1])
                path.append(self._move(route[idx], mid))
                path.append(self._move(mid, route[idx + 1]))
            else:
                raise ValueError("Invalid route node distance.")
        return path

    def _is_valid_node(self, i: int, j: int) -> bool:
        if not (0 <= i < self.merged_map_height and 0 <= j < self.merged_map_width):
            return False
        return (
            self.occ_map[2 * i][2 * j]
            and self.occ_map[2 * i + 1][2 * j]
            and self.occ_map[2 * i][2 * j + 1]
            and self.occ_map[2 * i + 1][2 * j + 1]
        )

    def _find_start(self, visit_times: Sequence[Sequence[int]]) -> Optional[GridPoint]:
        for i in range(self.merged_map_height):
            for j in range(self.merged_map_width):
                if self._is_valid_node(i, j) and visit_times[i][j] == 0:
                    return (i, j)
        return None

    def _perform_spanning_tree_coverage(
        self,
        current_node: GridPoint,
        visit_times: List[List[int]],
        route: List[GridPoint],
    ) -> List[GridPoint]:
        order = [(1, 0), (0, 1), (-1, 0), (0, -1)]
        found = False
        route.append(current_node)
        for inc in order:
            ni, nj = current_node[0] + inc[0], current_node[1] + inc[1]
            if self._is_valid_node(ni, nj) and visit_times[ni][nj] == 0:
                neighbor = (ni, nj)
                self.edge.append((current_node, neighbor))
                self.adj.setdefault(current_node, set()).add(neighbor)
                self.adj.setdefault(neighbor, set()).add(current_node)
                found = True
                visit_times[ni][nj] += 1
                self._perform_spanning_tree_coverage(neighbor, visit_times, route)

        if not found:
            for node in reversed(route):
                # if visit_times[node[0]][node[1]] == 2:
                #     continue
                visit_times[node[0]][node[1]] += 1
                route.append(node)
                for inc in order:
                    ni, nj = node[0] + inc[0], node[1] + inc[1]
                    if self._is_valid_node(ni, nj) and visit_times[ni][nj] == 0:
                        return route
        return route

    def _move(self, p: GridPoint, q: GridPoint) -> Tuple[GridPoint, GridPoint]:
        direction = self._get_vector_direction(p, q)
        if direction == "E":
            return self._get_sub_node(p, "SE"), self._get_sub_node(q, "SW")
        if direction == "W":
            return self._get_sub_node(p, "NW"), self._get_sub_node(q, "NE")
        if direction == "S":
            return self._get_sub_node(p, "SW"), self._get_sub_node(q, "NW")
        if direction == "N":
            return self._get_sub_node(p, "NE"), self._get_sub_node(q, "SE")
        raise ValueError("Unsupported move direction.")

    def _get_round_trip_path(self, last: GridPoint, pivot: GridPoint) -> Tuple[GridPoint, GridPoint]:
        direction = self._get_vector_direction(last, pivot)
        if direction == "E":
            return self._get_sub_node(pivot, "SE"), self._get_sub_node(pivot, "NE")
        if direction == "S":
            return self._get_sub_node(pivot, "SW"), self._get_sub_node(pivot, "SE")
        if direction == "W":
            return self._get_sub_node(pivot, "NW"), self._get_sub_node(pivot, "SW")
        if direction == "N":
            return self._get_sub_node(pivot, "NE"), self._get_sub_node(pivot, "NW")
        raise ValueError("Unsupported round-trip direction.")

    def _get_vector_direction(self, p: GridPoint, q: GridPoint) -> str:
        if p[0] == q[0] and p[1] < q[1]:
            return "E"
        if p[0] == q[0] and p[1] > q[1]:
            return "W"
        if p[0] < q[0] and p[1] == q[1]:
            return "S"
        if p[0] > q[0] and p[1] == q[1]:
            return "N"
        raise ValueError("Only axis-aligned moves are supported.")

    def _get_sub_node(self, node: GridPoint, direction: str) -> GridPoint:
        if direction == "SE":
            return (2 * node[0] + 1, 2 * node[1] + 1)
        if direction == "SW":
            return (2 * node[0] + 1, 2 * node[1])
        if direction == "NE":
            return (2 * node[0], 2 * node[1] + 1)
        if direction == "NW":
            return (2 * node[0], 2 * node[1])
        raise ValueError("Unknown sub-node direction.")

    def _get_intermediate_node(self, p: GridPoint, q: GridPoint) -> GridPoint:
        p_ngb = self.adj.get(p, set())
        q_ngb = self.adj.get(q, set())
        inter = p_ngb.intersection(q_ngb)
        if len(inter) != 1:
            raise ValueError("Intermediate node not found.")
        return next(iter(inter))


def _grid_path_to_polyline(path: Sequence[Tuple[GridPoint, GridPoint]]) -> List[GridPoint]:
    if not path:
        return []
    last = path[0][0]
    poly: List[GridPoint] = [last]
    for p, q in path:
        dist = math.hypot(p[0] - last[0], p[1] - last[1])
        if dist <= 1.0:
            if p != poly[-1]:
                poly.append(p)
        else:
            ipx, ipy = _get_interpolated_path(last, p)
            for row, col in zip(ipx, ipy):
                point = (row, col)
                if point != poly[-1]:
                    poly.append(point)
        last = q
    if last != poly[-1]:
        poly.append(last)
    return poly


def _get_interpolated_path(p: GridPoint, q: GridPoint) -> Tuple[List[int], List[int]]:
    if (p[0] < q[0]) ^ (p[1] < q[1]):
        ipx = [p[0], p[0], q[0]]
        ipy = [p[1], q[1], q[1]]
    else:
        ipx = [p[0], q[0], q[0]]
        ipy = [p[1], p[1], q[1]]
    return ipx, ipy


def spiral_coverage_from_polygon(
    polygon: Sequence[Sequence[float]],
    *,
    spacing: float,
    max_merged_nodes: int = 20000,
    padding_cells: int = 1,
) -> List[Point2D]:
    loop = _ensure_closed_loop(polygon)
    if len(loop) < 4:
        return []
    grid, origin_x, origin_y = _grid_from_polygon(loop, cell_size=spacing, padding_cells=padding_cells)

    merged_nodes = (len(grid) // 2) * (len(grid[0]) // 2)
    if merged_nodes <= 0 or merged_nodes > max_merged_nodes:
        return []

    desired_limit = max(sys.getrecursionlimit(), merged_nodes * 2 + 100)
    if desired_limit > sys.getrecursionlimit():
        sys.setrecursionlimit(desired_limit)

    planner = SpiralSpanningTreeCoveragePlanner(grid)
    path = planner.plan()
    polyline = _grid_path_to_polyline(path)

    points: List[Point2D] = []
    for row, col in polyline:
        x = origin_x + (col + 0.5) * spacing
        y = origin_y + (row + 0.5) * spacing
        points.append((x, y))
    return points


__all__ = ["spiral_coverage_from_polygon"]
