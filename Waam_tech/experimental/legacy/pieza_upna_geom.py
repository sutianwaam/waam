from __future__ import annotations

from pathlib import Path
from typing import List, Tuple, Optional
from math import hypot

from ..base import ExperimentalPlannerContext
from ...nc.waam_dsl_nc import parse_waam_dsl, WaamLayer
from ...core.config_loader import load_standard_config

Point = Tuple[float, float]


def _signed_area(points: List[Point]) -> float:
    if len(points) < 3:
        return 0.0
    pts = points[:]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def _ensure_closed(points: List[Point]) -> List[Point]:
    if not points:
        return points
    if points[0] != points[-1]:
        return points + [points[0]]
    return points


def _normalize_loop(points: List[Point], *, ccw: bool) -> List[Point]:
    pts = _ensure_closed(list(points))
    area = _signed_area(pts)
    if ccw and area < 0:
        pts = list(reversed(pts))
    if (not ccw) and area > 0:
        pts = list(reversed(pts))
    return pts


def _centroid(points: List[Point]) -> Point:
    pts = _ensure_closed(points)
    xs = [p[0] for p in pts[:-1]]
    ys = [p[1] for p in pts[:-1]]
    return (sum(xs) / max(1, len(xs)), sum(ys) / max(1, len(ys)))


def _rotate_loop_start(loop: List[Point], start_index: int) -> List[Point]:
    pts = _ensure_closed(loop)
    body = pts[:-1]
    if not body:
        return pts
    start_index %= len(body)
    rotated = body[start_index:] + body[:start_index]
    rotated.append(rotated[0])
    return rotated


def _nearest_vertex_index(loop: List[Point], ref: Point) -> int:
    body = _ensure_closed(loop)[:-1]
    return min(range(len(body)), key=lambda i: hypot(body[i][0] - ref[0], body[i][1] - ref[1]))


def _ordered_holes(holes: List[List[Point]]) -> List[List[Point]]:
    # top -> middle -> bottom using Y centroid descending
    return sorted(holes, key=lambda h: _centroid(h)[1], reverse=True)


def _bbox(points: List[Point]) -> Tuple[float, float, float, float]:
    pts = _ensure_closed(points)[:-1]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def _rect_loop(xmin: float, xmax: float, ymin: float, ymax: float, *, ccw: bool = True) -> List[Point]:
    pts = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]
    return _normalize_loop(pts, ccw=ccw)


class PiezaUpnaGeometryExperimentalPlanner:
    key = "pieza_upna"
    label = "Pieza_upna geometry experimental NC"
    output_filename = "waam_baseline_experimental.nc"

    def export_nc(self, context: ExperimentalPlannerContext) -> Path:
        dsl_path = context.output_dir / "dsl_waam" / "part.llm_waam"
        if not dsl_path.is_file():
            raise RuntimeError(f"DSL geometry not found: {dsl_path}")

        doc = parse_waam_dsl(dsl_path)
        cfg = load_standard_config(None)

        heights = cfg.get("heights", {}) if isinstance(cfg, dict) else {}
        clearance = float(heights.get("clearance") or 15.0)

        proc = (cfg.get("process") or {}).get("deposition", {}) if isinstance(cfg, dict) else {}
        interpass = (cfg.get("process") or {}).get("interpass", {}) if isinstance(cfg, dict) else {}
        defaults = (cfg.get("defaults") or {}) if isinstance(cfg, dict) else {}
        post = cfg.get("post") or {}

        travel_speed = float(proc.get("travel_speed_mm_min") or defaults.get("deposition", {}).get("travel_speed", 300.0))
        wire_feed = float(proc.get("wire_feed_mm_min") or defaults.get("deposition", {}).get("wire_feed", 3200.0))
        z_feed = float(defaults.get("roughing", {}).get("feed_z") or defaults.get("finishing", {}).get("feed_z") or 200.0)
        dwell_layer = float(proc.get("dwell_per_layer_s") or 1.0)
        cooling_time = float(interpass.get("max_dwell_s") or 5.0)
        arc_start_delay = float(post.get("arc_start_delay_s") or 0.4)
        arc_stop_delay = float(post.get("arc_stop_delay_s") or 0.2)
        gas_preflow = float(post.get("gas_preflow_s") or 1.0)
        gas_postflow = float(post.get("gas_postflow_s") or 2.0)
        torch_on = post.get("torch_on", "M62 P0")
        torch_off = post.get("torch_off", "M63 P0")
        gas_on = post.get("gas_on", "M64 P1")
        gas_off = post.get("gas_off", "M65 P1")
        wire_feed_cmd = (post.get("wire_feed") or "M68 E0 Q{wire_feed:.0f}").format(wire_feed=wire_feed)

        canonical_layer = self._select_canonical_layer(doc.layers)
        outer, holes = self._extract_canonical_loops(canonical_layer)
        helper_paths = self._build_band_aware_helpers(outer, holes)

        target = context.output_dir / "nc_files" / self.output_filename
        target.parent.mkdir(parents=True, exist_ok=True)

        preamble = [
            "%",
            "O9001 (WAAM EXP PIEZA_UPNA GEOMETRY)",
            f"(Source: {dsl_path.name})",
            "(OPTIMIZATIONS: GEOMETRY-DRIVEN, THIN-WALL FRAME, 3 HOLES PRESERVED, BAND-AWARE HELPERS IN TOP/BOTTOM/CONNECTOR REGIONS, NO DEPOSITED CONNECTORS THROUGH VOIDS)",
            f"(WIRE FEED SPEED: {wire_feed/1000.0:.1f} m/min | TRAVEL SPEED: {travel_speed:.0f} mm/min)",
            "G21 G90 G94",
            "G17",
            "G40 G49 G80",
            f"G0 Z{clearance:.3f}",
            "",
        ]
        footer = [
            torch_off,
            gas_off,
            f"G0 Z{clearance:.3f}",
            "G0 X0.0 Y0.0",
            "M30",
            "%",
        ]

        lines = list(preamble)
        layers_sorted = sorted(doc.layers, key=lambda l: (l.z if l.z is not None else l.index, l.index))
        layer_height = doc.layer_height or 1.0
        z_values = [layer.z for layer in layers_sorted if layer.z is not None]
        z_shift = -min(z_values) if z_values and abs(min(z_values)) > 1e-6 else 0.0

        prev_end: Optional[Point] = None
        for layer_idx, layer in enumerate(layers_sorted, start=1):
            z_val = layer.z if layer.z is not None else layer.index * layer_height
            z_val += z_shift
            reverse_outer = (layer_idx % 2 == 0)

            layer_paths: List[Tuple[str, List[Point], bool]] = []
            out_loop = list(reversed(outer)) if reverse_outer else list(outer)
            layer_paths.append(("OUTER", out_loop, True))

            ordered_holes = _ordered_holes([list(h) for h in holes])
            for j, hole in enumerate(ordered_holes):
                rev = ((layer_idx + j) % 2 == 1)
                loop = list(reversed(hole)) if rev else list(hole)
                layer_paths.append((f"HOLE_{j+1}", loop, True))

            for j, (role, pts, closed) in enumerate(helper_paths):
                rev = ((layer_idx + j + 1) % 2 == 0)
                seq = list(reversed(pts)) if rev else list(pts)
                layer_paths.append((role, seq, closed))

            if prev_end is not None:
                reordered = []
                remaining = layer_paths[:]
                current = prev_end
                while remaining:
                    best_idx = min(
                        range(len(remaining)),
                        key=lambda i: hypot(remaining[i][1][0][0] - current[0], remaining[i][1][0][1] - current[1])
                    )
                    role, pts, closed = remaining.pop(best_idx)
                    if closed:
                        idx0 = _nearest_vertex_index(pts, current)
                        pts = _rotate_loop_start(pts, idx0)
                    reordered.append((role, pts, closed))
                    current = pts[-1]
                layer_paths = reordered

            lines.append(f"(--- LAYER {layer.index} Z={z_val:.3f} ---)")
            for role, pts_in, closed in layer_paths:
                pts = _ensure_closed(pts_in) if closed else list(pts_in)
                if len(pts) < 2:
                    continue
                start = pts[0]
                lines.append(f"(Path {role})")
                lines.append(f"G0 X{start[0]:.3f} Y{start[1]:.3f} Z{clearance:.3f}")
                lines.append(gas_on)
                lines.append(f"G4 P{gas_preflow:.1f}")
                lines.append(wire_feed_cmd)
                lines.append(torch_on)
                lines.append(f"G4 P{arc_start_delay:.1f}")
                lines.append(f"G1 Z{z_val:.3f} F{z_feed:.1f}")
                lines.append(f"F{travel_speed:.0f}")
                for x, y in pts[1:]:
                    lines.append(f"G1 X{x:.3f} Y{y:.3f}")
                if closed and pts[-1] != pts[0]:
                    lines.append(f"G1 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}")
                lines.append(f"G4 P{dwell_layer:.1f}")
                lines.append(torch_off)
                lines.append(f"G4 P{arc_stop_delay:.1f}")
                lines.append(gas_off)
                lines.append(f"G4 P{gas_postflow:.1f}")
                lines.append(f"G0 Z{clearance:.3f}")
                lines.append("")
                prev_end = pts[-1]
            lines.append(f"G4 P{cooling_time:.1f}")
            lines.append("")

        lines.extend(footer)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    def _select_canonical_layer(self, layers: List[WaamLayer]) -> WaamLayer:
        for layer in layers:
            if len(layer.contours) >= 4:
                return layer
        raise RuntimeError("No suitable canonical layer found for Pieza_upna geometry export.")

    def _extract_canonical_loops(self, layer: WaamLayer) -> Tuple[List[Point], List[List[Point]]]:
        outers = [c for c in layer.contours if c.is_outer]
        inners = [c for c in layer.contours if not c.is_outer]
        if len(outers) != 1 or len(inners) != 3:
            raise RuntimeError(
                f"Unexpected Pieza_upna topology: expected 1 outer + 3 inner loops, got {len(outers)} outer and {len(inners)} inner."
            )
        outer = _normalize_loop(outers[0].points, ccw=True)
        holes = [_normalize_loop(c.points, ccw=False) for c in inners]
        return outer, holes

    def _build_band_aware_helpers(self, outer: List[Point], holes: List[List[Point]]) -> List[Tuple[str, List[Point], bool]]:
        """
        Build helper paths in the actual useful bands for this part:
        - top band
        - bottom band
        - upper connector band
        - lower connector band

        Use open helper interlines rather than pretending everything should be a closed side loop.
        """
        outer_xmin, outer_xmax, outer_ymin, outer_ymax = _bbox(outer)
        ordered = _ordered_holes([list(h) for h in holes])
        top_hole, mid_hole, bottom_hole = ordered[0], ordered[1], ordered[2]
        top_b = _bbox(top_hole)
        mid_b = _bbox(mid_hole)
        bottom_b = _bbox(bottom_hole)

        margin = 0.7
        x0 = outer_xmin + 1.0
        x1 = outer_xmax - 1.0
        helpers: List[Tuple[str, List[Point], bool]] = []

        def add_horizontal_band(role: str, y0: float, y1: float) -> None:
            if y1 - y0 < 0.9:
                return
            ymid = 0.5 * (y0 + y1)
            helpers.append((role, [(x0, ymid), (x1, ymid)], False))

        # Top cap: between top hole top and outer top
        add_horizontal_band("HELPER_TOP", top_b[3] + margin, outer_ymax - margin)

        # Bottom cap: between outer bottom and bottom hole bottom
        add_horizontal_band("HELPER_BOTTOM", outer_ymin + margin, bottom_b[2] - margin)

        # Connector between top hole and middle hole
        upper_conn_y0 = mid_b[3] + margin
        upper_conn_y1 = top_b[2] - margin
        add_horizontal_band("HELPER_UPPER_CONNECTOR", upper_conn_y0, upper_conn_y1)

        # Connector between middle hole and bottom hole
        lower_conn_y0 = bottom_b[3] + margin
        lower_conn_y1 = mid_b[2] - margin
        add_horizontal_band("HELPER_LOWER_CONNECTOR", lower_conn_y0, lower_conn_y1)

        return helpers
