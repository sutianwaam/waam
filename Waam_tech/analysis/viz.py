"""Lightweight plotting helpers for WAAM NC attempts."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

try:  # Optional; plotting is best-effort.
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
except Exception:  # pragma: no cover - optional dependency
    plt = None


Point2D = Tuple[float, float]
Point3D = Tuple[float, float, float]


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_gcode_lines(gcode_text: str) -> List[str]:
    lines: List[str] = []
    for raw in gcode_text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if "(" in stripped and ")" in stripped:
            # Remove inline comment blocks
            while "(" in stripped and ")" in stripped:
                start = stripped.index("(")
                end = stripped.index(")")
                stripped = (stripped[:start] + stripped[end + 1 :]).strip()
        if stripped:
            lines.append(stripped)
    return lines


def _extract_moves(lines: Sequence[str]) -> Tuple[List[Tuple[Point3D, Point3D]], List[Tuple[Point3D, Point3D]]]:
    travel: List[Tuple[Point3D, Point3D]] = []
    depo: List[Tuple[Point3D, Point3D]] = []
    x = y = z = 0.0
    torch_on = False
    for line in lines:
        if line.startswith("M62"):
            torch_on = True
            continue
        if line.startswith("M63"):
            torch_on = False
            continue
        if line.startswith("G0") or line.startswith("G1"):
            prev = (x, y, z)
            tokens = [token for token in line.split() if token and token[0] in {"X", "Y", "Z"}]
            for token in tokens:
                axis, val = token[0], token[1:]
                if axis == "X":
                    x = _as_float(val, x)
                elif axis == "Y":
                    y = _as_float(val, y)
                elif axis == "Z":
                    z = _as_float(val, z)
            curr = (x, y, z)
            if prev != curr:
                (depo if torch_on else travel).append((prev, curr))
    return travel, depo


def _layer_reference(job_layers, job_paths: Dict[str, Sequence[Point2D]]) -> Dict[int, List[Point2D]]:
    ref: Dict[int, List[Point2D]] = {}
    for layer in job_layers:
        pts: List[Point2D] = []
        for pid in getattr(layer, "path_ids", []) or []:
            pts.extend(job_paths.get(pid, []))
        ref[layer.index] = pts
    return ref


def plot_attempt(gcode_text: str, job, attempt_dir: Path, iteration: int) -> Path | None:
    """Plot 2D (per layer) and 3D accumulation for a single attempt."""
    if plt is None:
        return None
    lines = _parse_gcode_lines(gcode_text)
    travel, depo = _extract_moves(lines)

    fig = plt.figure(figsize=(12, 6))
    ax2d = fig.add_subplot(1, 2, 1)
    for start, end in travel:
        ax2d.plot([start[0], end[0]], [start[1], end[1]], "k--", linewidth=0.7, alpha=0.5)
    for start, end in depo:
        ax2d.plot([start[0], end[0]], [start[1], end[1]], "r-", linewidth=1.4)
    # Overlay reference paths for quick human inspection
    ref = _layer_reference(job.layers, job.paths)
    for idx, pts in ref.items():
        if not pts:
            continue
        xs, ys = zip(*pts)
        ax2d.plot(xs, ys, linestyle=":", linewidth=0.9, label=f"Ref L{idx}")
    ax2d.set_title(f"Attempt {iteration} - XY")
    ax2d.set_xlabel("X (mm)")
    ax2d.set_ylabel("Y (mm)")
    ax2d.axis("equal")
    if ref:
        ax2d.legend(loc="best", fontsize=7)

    ax3d = fig.add_subplot(1, 2, 2, projection="3d")
    for start, end in depo:
        ax3d.plot([start[0], end[0]], [start[1], end[1]], [start[2], end[2]], color="red")
    ax3d.set_title("Deposition 3D")
    ax3d.set_xlabel("X (mm)")
    ax3d.set_ylabel("Y (mm)")
    ax3d.set_zlabel("Z (mm)")

    attempt_dir.mkdir(parents=True, exist_ok=True)
    out_path = attempt_dir / f"attempt_{iteration}_plot.png"
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)
    return out_path
