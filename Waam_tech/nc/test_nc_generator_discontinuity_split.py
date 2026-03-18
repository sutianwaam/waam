from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from Waam_tech.nc.nc_generator import generate_baseline_nc


def _segment_circle_distance(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    center: Tuple[float, float],
) -> float:
    x1, y1 = p0
    x2, y2 = p1
    cx, cy = center
    dx = x2 - x1
    dy = y2 - y1
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return math.hypot(x1 - cx, y1 - cy)
    t = ((cx - x1) * dx + (cy - y1) * dy) / denom
    t = max(0.0, min(1.0, t))
    px = x1 + t * dx
    py = y1 + t * dy
    return math.hypot(px - cx, py - cy)


def _deposition_segments(gcode_path: Path) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    lines = gcode_path.read_text(encoding="utf-8").splitlines()
    x = y = z = 0.0
    torch_on = False
    out: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []

    for line in lines:
        text = line.strip()
        if not text:
            continue
        if text.startswith("M62 P0"):
            torch_on = True
            continue
        if text.startswith("M63 P0"):
            torch_on = False
            continue

        if not (text.startswith("G0") or text.startswith("G1")):
            continue

        nx, ny, nz = x, y, z
        mx = re.search(r"X(-?\d+(?:\.\d+)?)", text)
        my = re.search(r"Y(-?\d+(?:\.\d+)?)", text)
        mz = re.search(r"Z(-?\d+(?:\.\d+)?)", text)
        if mx:
            nx = float(mx.group(1))
        if my:
            ny = float(my.group(1))
        if mz:
            nz = float(mz.group(1))

        if torch_on and text.startswith("G1") and (mx or my):
            out.append(((x, y), (nx, ny)))

        x, y, z = nx, ny, nz

    return out


def _crossings(segments: Sequence[Tuple[Tuple[float, float], Tuple[float, float]]], center: Tuple[float, float], radius: float) -> int:
    hits = 0
    for p0, p1 in segments:
        if _segment_circle_distance(p0, p1, center) < max(0.0, radius - 0.05):
            hits += 1
    return hits


def _write_ops(tmp_path: Path) -> Path:
    ops = {
        "metadata": {
            "units": "mm",
            "clearance": 10.0,
            "rapidZ": 2.0,
        },
        "process_parameters": {
            "deposition": {
                "layer_height_mm": 1.0,
                "bead_width_mm": 1.0,
                "travel_speed_mm_min": 300.0,
                "wire_feed_mm_min": 100.0,
                "dwell_per_layer_s": 0.0,
            },
            "interpass": {
                "max_dwell_s": 0.0,
            },
        },
        "feature_index": {
            "region_0": {
                "id": "region_0",
                "type": "planar_pocket_floor",
                "bbox": [0.0, 10.0, -1.0, 1.0],
            },
            "hole_0": {
                "id": "hole_0",
                "type": "through_hole",
                "center": [5.0, 0.0, 1.0],
                "diameter": 2.0,
                "depth": 2.0,
                "z_top": 1.0,
            },
        },
        "sequence": [
            {
                "op": "deposit_infill",
                "feature_id": "region_0",
                "region": "region_0",
                "layer_index": 0,
                "layer_z_mm": 0.0,
                "sequence_index": 0,
                "trajectory": {
                    "points": [
                        [0.0, 0.0, 0.0, 0.0],
                        [10.0, 0.0, 0.0, 10.0],
                    ]
                },
                "params": {
                    "travel_speed_mm_min": 300.0,
                    "wire_feed_mm_min": 100.0,
                },
            }
        ],
    }
    ops_path = tmp_path / "waam_operations.json"
    ops_path.write_text(json.dumps(ops, indent=2), encoding="utf-8")
    return ops_path


def test_auto_mode_avoids_deposition_through_hole(tmp_path: Path) -> None:
    ops_path = _write_ops(tmp_path)
    nc_path = tmp_path / "auto.nc"
    generate_baseline_nc(ops_path, output_path=nc_path, discontinuity_split_mode="auto")

    segments = _deposition_segments(nc_path)
    assert _crossings(segments, (5.0, 0.0), 1.0) == 0


def test_never_mode_keeps_continuous_move_across_hole(tmp_path: Path) -> None:
    ops_path = _write_ops(tmp_path)
    nc_path = tmp_path / "never.nc"
    generate_baseline_nc(ops_path, output_path=nc_path, discontinuity_split_mode="never")

    segments = _deposition_segments(nc_path)
    assert _crossings(segments, (5.0, 0.0), 1.0) > 0
