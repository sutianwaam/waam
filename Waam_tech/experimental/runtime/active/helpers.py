from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

ACTIVE_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ACTIVE_ROOT / "manifest.json"
NOTES_PATH = ACTIVE_ROOT / "notes.md"


Point2D = Tuple[float, float]
PathPts = List[Point2D]


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_manifest():
    if not MANIFEST_PATH.is_file():
        return {}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def append_iteration_note(context, *, heading: str, lines) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not NOTES_PATH.exists():
        NOTES_PATH.write_text("# Active Experiment Notes\n\n", encoding="utf-8")
    with NOTES_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"## {heading} ({_timestamp()})\n")
        for line in lines:
            handle.write(f"{line}\n")
        handle.write("\n")


def experimental_output_path(context) -> Path:
    target = context.output_dir / "nc_files" / "waam_baseline_experimental.nc"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def read_baseline_lines(context):
    p = Path(context.baseline_nc_path) if context.baseline_nc_path else context.output_dir / 'nc_files' / 'waam_baseline.nc'
    return p.read_text(encoding='utf-8', errors='ignore').splitlines()


def write_experimental_lines(context, lines) -> Path:
    target = experimental_output_path(context)
    target.write_text("\n".join(lines) + "\n", encoding='utf-8')
    return target


def _extract_layer_blocks(lines):
    blocks = []
    current = None
    for line in lines:
        m = re.match(r"\(--- LAYER\s+(\d+)\s+Z=\s*([0-9.+-]+)", line.strip())
        if m:
            if current is not None:
                blocks.append(current)
            current = {"index": int(m.group(1)), "z": float(m.group(2)), "lines": [line]}
        elif current is not None:
            current["lines"].append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def _program_header(lines):
    out = []
    for line in lines:
        out.append(line)
        if line.strip() == '( --- START SEQUENCE --- )':
            break
    return out


def _program_footer(lines):
    for i, line in enumerate(lines):
        if line.strip() == 'M30':
            return lines[i - 2:] if i >= 2 else lines[i:]
    return ['M30', '%']


def _parse_xy(line: str):
    x = y = None
    for token in line.split():
        if token.startswith('X'):
            try: x = float(token[1:])
            except Exception: pass
        elif token.startswith('Y'):
            try: y = float(token[1:])
            except Exception: pass
    return x, y


def _collect_deposition_paths(block_lines):
    paths = []
    current_path = None
    current_pts: PathPts = []
    arc_on = False
    last_x = last_y = None
    for line in block_lines:
        s = line.strip()
        m = re.match(r'\(Path\s+(.+)\)', s)
        if m:
            if current_path and len(current_pts) >= 2:
                paths.append((current_path, current_pts))
            current_path = m.group(1)
            current_pts = []
            continue
        if s.startswith('M62 P0'):
            arc_on = True
            continue
        if s.startswith('M63 P0'):
            arc_on = False
            continue
        if s.startswith(('G0', 'G1')):
            x, y = _parse_xy(s)
            x = x if x is not None else last_x
            y = y if y is not None else last_y
            if arc_on and s.startswith('G1') and x is not None and y is not None:
                current_pts.append((x, y))
            last_x, last_y = x, y
    if current_path and len(current_pts) >= 2:
        paths.append((current_path, current_pts))
    return paths


def _classify_template_paths(paths):
    contours = []
    infills = []
    for pid, pts in paths:
        if 'TOPO_INFILL' in pid:
            infills.append((pid, pts))
        else:
            contours.append((pid, pts))
    return contours, infills


def _path_start(pts: PathPts) -> Point2D:
    return pts[0]


def _path_end(pts: PathPts) -> Point2D:
    return pts[-1]


def _dist(a: Point2D, b: Point2D) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _reverse_pts(pts: PathPts) -> PathPts:
    return list(reversed(pts))


def _reorder_paths_locally(path_entries):
    if not path_entries:
        return []
    remaining = [(name, list(pts)) for name, pts in path_entries]
    ordered = [remaining.pop(0)]
    current_end = _path_end(ordered[0][1])
    while remaining:
        best_idx = None
        best_cost = None
        best_rev = False
        for i, (name, pts) in enumerate(remaining):
            d_fwd = _dist(current_end, _path_start(pts))
            d_rev = _dist(current_end, _path_end(pts))
            if best_cost is None or d_fwd < best_cost:
                best_cost = d_fwd
                best_idx = i
                best_rev = False
            if d_rev < best_cost:
                best_cost = d_rev
                best_idx = i
                best_rev = True
        name, pts = remaining.pop(best_idx)
        if best_rev:
            pts = _reverse_pts(pts)
        ordered.append((name, pts))
        current_end = _path_end(pts)
    return ordered


def _emit_path(lines, path_name, pts, z, *, feed=321.0, z_feed=112.2, wire_cmd='M68 E0 Q3731'):
    if len(pts) < 2:
        return
    sx, sy = pts[0]
    lines.append(f'(Path {path_name})')
    lines.append(f'G0 X{sx:.3f} Y{sy:.3f} Z119.600')
    lines.append('M64 P1')
    lines.append('G4 P1.0')
    lines.append(wire_cmd)
    lines.append(f'G1 Z{z:.3f} F{z_feed:.1f}')
    lines.append('M62 P0')
    lines.append('G4 P0.4')
    first = True
    for x, y in pts[1:]:
        if first:
            lines.append(f'G1 X{x:.3f} Y{y:.3f} F{feed:.0f}')
            first = False
        else:
            lines.append(f'G1 X{x:.3f} Y{y:.3f}')
    lines.append('G4 P1.5')
    lines.append('M63 P0')
    lines.append('G4 P0.2')
    lines.append('M65 P1')
    lines.append('G4 P2.0')
    ex, ey = pts[-1]
    lines.append(f'G0 X{ex:.3f} Y{ey:.3f} Z119.600')
    lines.append('')


def export_constant_section_two_hole_nc(context) -> Path:
    lines = read_baseline_lines(context)
    header = _program_header(lines)
    footer = _program_footer(lines)
    blocks = _extract_layer_blocks(lines)
    if not blocks:
        return write_experimental_lines(context, lines)

    template_paths = _collect_deposition_paths(blocks[0]['lines'])
    contours, infills = _classify_template_paths(template_paths)
    ordered_contours = _reorder_paths_locally(contours)
    ordered_infills = _reorder_paths_locally(infills)

    out = list(header)
    for block in blocks:
        out.append(f'(--- LAYER {block["index"]} Z={block["z"]:.3f} ---)')
        for idx, (_pid, pts) in enumerate(ordered_contours, start=1):
            _emit_path(out, f'CONST_CONTOUR_{block["index"]}_{idx}', pts, block['z'])
        for idx, (_pid, pts) in enumerate(ordered_infills, start=1):
            _emit_path(out, f'CONST_INFILL_{block["index"]}_{idx}', pts, block['z'])

    out.extend(footer)
    append_iteration_note(
        context,
        heading='Experimental constant cross-section repeated-layer export',
        lines=[
            f'- template layer index: {blocks[0]["index"]}',
            f'- template contours: {len(contours)}',
            f'- template infill paths: {len(infills)}',
            '- strategy: repeat one constant cross-section layer pattern upward while reordering template paths locally to reduce long travel diagonals',
        ],
    )
    return write_experimental_lines(context, out)
