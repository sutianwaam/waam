from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple
import re

from .base import ExperimentalPlannerContext


def _parse_layer_z_from_header(line: str) -> Optional[float]:
    m = re.search(r"Z=([0-9.]+)", line)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def _segment_stats(lines: List[str]) -> Tuple[int, float, float, float, float]:
    xs = []
    ys = []
    g1_count = 0
    for line in lines:
        if line.startswith("G1 "):
            g1_count += 1
        for mx in re.finditer(r"X([-+]?\d*\.?\d+)", line):
            xs.append(float(mx.group(1)))
        for my in re.finditer(r"Y([-+]?\d*\.?\d+)", line):
            ys.append(float(my.group(1)))
    if not xs or not ys:
        return g1_count, 0.0, 0.0, 0.0, 0.0
    return g1_count, min(xs), max(xs), min(ys), max(ys)


def _is_bulk_strip_candidate(lines: List[str]) -> bool:
    """
    Heuristic for the bad Pieza_upna baseline behavior:
    very long path blocks with little X variation and large Y span,
    behaving like generic strip fill across a thin-wall profile.
    """
    g1_count, xmin, xmax, ymin, ymax = _segment_stats(lines)
    xspan = xmax - xmin
    yspan = ymax - ymin
    if g1_count < 80:
        return False
    # Tall, mostly strip-like motion with weak X spread.
    if xspan <= 3.0 and yspan >= 15.0:
        return True
    # Extremely long blocks are suspicious even with slightly larger xspan.
    if g1_count >= 140 and xspan <= 5.0 and yspan >= 12.0:
        return True
    return False


def _is_small_loop_or_boundary(lines: List[str]) -> bool:
    """Keep compact loops and local boundary-like paths; they are more likely to represent openings or contours."""
    g1_count, xmin, xmax, ymin, ymax = _segment_stats(lines)
    xspan = xmax - xmin
    yspan = ymax - ymin
    if 8 <= g1_count <= 120 and xspan <= 8.0 and yspan <= 8.0:
        return True
    if g1_count <= 180 and xspan > 0.0 and yspan > 0.0 and (xspan + yspan) <= 25.0:
        return True
    return False


class PiezaUpnaExperimentalPlanner:
    key = "pieza_upna"
    label = "Pieza_upna experimental NC"
    output_filename = "waam_baseline_experimental.nc"

    def export_nc(self, context: ExperimentalPlannerContext) -> Path:
        nc_dir = context.output_dir / "nc_files"
        nc_dir.mkdir(parents=True, exist_ok=True)
        target = nc_dir / self.output_filename

        baseline = nc_dir / "waam_baseline.nc"
        if not baseline.is_file():
            raise RuntimeError("No waam_baseline.nc found for Pieza_upna experimental export.")

        lines = baseline.read_text(encoding="utf-8", errors="ignore").splitlines()
        out: List[str] = []
        current_z: Optional[float] = None
        buffered_header: Optional[str] = None
        buffered_lines: List[str] = []

        def flush_buffer() -> None:
            nonlocal buffered_header, buffered_lines
            if buffered_header is None:
                return

            keep = True
            path_name = buffered_header[6:-1] if buffered_header.startswith("(Path ") else "UNKNOWN"

            # Strategy for Pieza_upna:
            # - preserve boundary/loop-like paths
            # - suppress bulk strip candidates
            # - keep compact internal loops that likely correspond to openings
            # - on this part, all layers share the same topology, so no need for aggressive area fill
            if _is_bulk_strip_candidate(buffered_lines):
                keep = False
            elif _is_small_loop_or_boundary(buffered_lines):
                keep = True

            if keep:
                out.append(buffered_header)
                out.extend(buffered_lines)
            else:
                out.append(
                    f"(SKIPPED {path_name} IN PIEZA_UPNA EXP: suppressed bulk-style strip path; preserving thin-wall/frame logic with openings)"
                )

            buffered_header = None
            buffered_lines = []

        for line in lines:
            if line.startswith("(PROGRAM: "):
                out.append("(PROGRAM: WAAM_EXP_PIEZA_UPNA.NC)")
                continue

            if line.startswith("(OPTIMIZATIONS:"):
                out.append(
                    "(OPTIMIZATIONS: PIEZA_UPNA EXPERIMENTAL, CONSTANT-Z THIN-WALL FRAME, HOLE-PRESERVING, WALL-AWARE, CONTINUITY-FIRST, NO BULK-STYLE STRIP FILL)"
                )
                continue

            if line.startswith("(WIRE FEED SPEED:"):
                out.append(line)
                out.append(
                    "(EXPERIMENTAL DIFFERENCE: suppress generic strip-fill behavior; preserve contour/loop-like paths better suited to a constant-profile thin-wall frame with 3 persistent openings.)"
                )
                continue

            if line.startswith("(--- LAYER "):
                flush_buffer()
                current_z = _parse_layer_z_from_header(line)
                out.append(line)
                continue

            if line.startswith("(Path "):
                flush_buffer()
                buffered_header = line
                buffered_lines = []
                continue

            if buffered_header is not None:
                buffered_lines.append(line)
            else:
                out.append(line)

        flush_buffer()
        target.write_text("\n".join(out) + "\n", encoding="utf-8")
        return target
