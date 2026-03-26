from __future__ import annotations

from pathlib import Path
from typing import List, Tuple, Optional
from math import hypot
import json

from ..base import ExperimentalPlannerContext
from ...nc.waam_dsl_nc import parse_waam_dsl, WaamLayer
from ...core.config_loader import load_standard_config

Point = Tuple[float, float]
_HOLE_FEATURE_TYPES = {"hole", "through_hole", "cylindrical_hole"}


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


def _nearest_vertex_index(loop: List[Point], ref: Point) -> int:
    body = _ensure_closed(loop)[:-1]
    return min(range(len(body)), key=lambda i: hypot(body[i][0] - ref[0], body[i][1] - ref[1]))


def _rotate_loop_start(loop: List[Point], start_index: int) -> List[Point]:
    pts = _ensure_closed(loop)
    body = pts[:-1]
    start_index %= len(body)
    rotated = body[start_index:] + body[:start_index]
    rotated.append(rotated[0])
    return rotated


def _bbox(points: List[Point]) -> Tuple[float, float, float, float]:
    pts = _ensure_closed(points)[:-1]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def _rect_loop(xmin: float, xmax: float, ymin: float, ymax: float, *, ccw: bool = True) -> List[Point]:
    pts = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]
    return _normalize_loop(pts, ccw=ccw)


class StudyCadCam78ExperimentalPlanner:
    key = "studycadcam_78"
    label = "StudyCadCam 78 experimental NC"
    output_filename = "waam_baseline_experimental.nc"

    def export_nc(self, context: ExperimentalPlannerContext) -> Path:
        dsl_path = context.output_dir / "dsl_waam" / "part.llm_waam"
        feat_path = context.output_dir / "features" / "features.json"
        if not dsl_path.is_file():
            raise RuntimeError(f"DSL geometry not found: {dsl_path}")
        if not feat_path.is_file():
            raise RuntimeError(f"Feature inventory not found: {feat_path}")

        doc = parse_waam_dsl(dsl_path)
        cfg = load_standard_config(None)
        features = json.loads(feat_path.read_text())

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
        outer, direct_holes = self._extract_canonical_loops(canonical_layer)
        projected_holes = self._build_projected_cross_axis_holes(features, outer, direct_holes)
        all_holes = direct_holes + projected_holes
        helper_paths = self._build_base_helper_paths(outer, all_holes)

        target = context.output_dir / "nc_files" / self.output_filename
        target.parent.mkdir(parents=True, exist_ok=True)

        preamble = [
            "%",
            "O9002 (WAAM EXP STUDYCADCAM 78)",
            f"(Source: {dsl_path.name})",
            "(OPTIMIZATIONS: 4-HOLE-AWARE, DIRECT SLICE HOLES + PROJECTED CROSS-AXIS HOLE MASKS, EXPERIMENTAL ONLY)",
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
            layer_loops: List[Tuple[str, List[Point]]] = []

            out_loop = list(reversed(outer)) if (layer_idx % 2 == 0) else list(outer)
            layer_loops.append(("OUTER", out_loop))

            ordered_holes = sorted([list(h) for h in all_holes], key=lambda h: _centroid(h)[1], reverse=True)
            for j, hole in enumerate(ordered_holes):
                rev = ((layer_idx + j) % 2 == 1)
                loop = list(reversed(hole)) if rev else list(hole)
                label = f"HOLE_{j+1}"
                layer_loops.append((label, loop))

            for j, helper in enumerate(helper_paths):
                rev = ((layer_idx + j + 1) % 2 == 0)
                loop = list(reversed(helper)) if rev else list(helper)
                layer_loops.append((f"BASE_{j+1}", loop))

            if prev_end is not None:
                reordered = []
                remaining = layer_loops[:]
                current = prev_end
                while remaining:
                    best_idx = min(
                        range(len(remaining)),
                        key=lambda i: hypot(remaining[i][1][0][0] - current[0], remaining[i][1][0][1] - current[1])
                    )
                    role, loop = remaining.pop(best_idx)
                    idx0 = _nearest_vertex_index(loop, current)
                    loop = _rotate_loop_start(loop, idx0)
                    reordered.append((role, loop))
                    current = loop[-1]
                layer_loops = reordered

            lines.append(f"(--- LAYER {layer.index} Z={z_val:.3f} ---)")
            for role, loop in layer_loops:
                pts = _ensure_closed(loop)
                if len(pts) < 4:
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
            if len(layer.contours) >= 3:
                return layer
        raise RuntimeError("No suitable canonical layer found for StudyCadCam 78.")

    def _extract_canonical_loops(self, layer: WaamLayer) -> Tuple[List[Point], List[List[Point]]]:
        outers = [c for c in layer.contours if c.is_outer]
        inners = [c for c in layer.contours if not c.is_outer]
        if len(outers) != 1:
            raise RuntimeError(f"Unexpected topology: expected 1 outer loop, got {len(outers)}")
        outer = _normalize_loop(outers[0].points, ccw=True)
        holes = [_normalize_loop(c.points, ccw=False) for c in inners]
        return outer, holes

    def _build_projected_cross_axis_holes(self, features: dict, outer: List[Point], direct_holes: List[List[Point]]) -> List[List[Point]]:
        projected: List[List[Point]] = []
        existing_centroids = [_centroid(h) for h in direct_holes]
        outer_xmin, outer_xmax, outer_ymin, outer_ymax = _bbox(outer)

        for feat in features.get('features', []):
            if str(feat.get('type') or '').strip().lower() not in _HOLE_FEATURE_TYPES:
                continue
            axis = feat.get('axis_wcs') or feat.get('axis') or [0, 0, 1]
            center = feat.get('center') or feat.get('center_wcs')
            diameter = feat.get('diameter') or feat.get('diameter_mm') or feat.get('radius', 0) * 2
            if not center or not diameter:
                continue

            # Non-vertical / cross-axis holes should become projected masks.
            if abs(axis[2]) > 0.5:
                continue

            cx, cy = float(center[0]), float(center[1])
            r = max(0.8, float(diameter) / 2.0)

            # Skip if already represented by a direct slice-native hole.
            if any(hypot(cx - ex, cy - ey) < max(1.0, r) for ex, ey in existing_centroids):
                continue

            xmin = max(outer_xmin + 0.6, cx - r)
            xmax = min(outer_xmax - 0.6, cx + r)
            ymin = max(outer_ymin + 0.6, cy - r)
            ymax = min(outer_ymax - 0.6, cy + r)
            if xmax - xmin < 0.8 or ymax - ymin < 0.8:
                continue
            projected.append(_rect_loop(xmin, xmax, ymin, ymax, ccw=False))

        return projected

    def _build_base_helper_paths(self, outer: List[Point], holes: List[List[Point]]) -> List[List[Point]]:
        """
        Add simple base-region deposition loops while preserving all hole masks.

        Current experimental assumption from the screenshot:
        the base is under-deposited, so add a couple of inset helper rectangles
        clipped to remain outside the union hole envelope.
        """
        outer_xmin, outer_xmax, outer_ymin, outer_ymax = _bbox(outer)
        hb = [_bbox(h) for h in holes]
        holes_xmin = min(b[0] for b in hb)
        holes_xmax = max(b[1] for b in hb)
        holes_ymin = min(b[2] for b in hb)
        holes_ymax = max(b[3] for b in hb)

        loops: List[List[Point]] = []
        margin = 1.0

        # Bottom/base band: material below the lower envelope of the holes.
        band_y0 = outer_ymin + margin
        band_y1 = holes_ymin - margin
        if band_y1 - band_y0 > 2.0:
            loops.append(_rect_loop(outer_xmin + margin, outer_xmax - margin, band_y0, band_y1, ccw=True))
            inner_margin = 2.0
            if band_y1 - band_y0 > 4.0 and (outer_xmax - outer_xmin) > 2 * inner_margin + 2.0:
                loops.append(_rect_loop(outer_xmin + inner_margin, outer_xmax - inner_margin, band_y0 + 0.8, band_y1 - 0.8, ccw=True))

        # Back wall / upper band above the hole field may also deserve one helper if wide enough.
        top_y0 = holes_ymax + margin
        top_y1 = outer_ymax - margin
        if top_y1 - top_y0 > 2.0:
            loops.append(_rect_loop(outer_xmin + margin, outer_xmax - margin, top_y0, top_y1, ccw=True))

        # Optional central shelf band if there is a safe corridor between left/right hole extents.
        # Keep it conservative: only add if holes leave a central material window.
        left_width = holes_xmin - outer_xmin
        right_width = outer_xmax - holes_xmax
        if left_width > 3.0:
            loops.append(_rect_loop(outer_xmin + margin, holes_xmin - margin, holes_ymin + margin, holes_ymax - margin, ccw=True))
        if right_width > 3.0:
            loops.append(_rect_loop(holes_xmax + margin, outer_xmax - margin, holes_ymin + margin, holes_ymax - margin, ccw=True))

        return loops
