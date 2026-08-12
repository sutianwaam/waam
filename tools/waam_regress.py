#!/usr/bin/env python3
"""Compare a candidate NC file against a reference NC file (checkpoint C).

Usage:
    python tools/waam_regress.py candidate.nc reference.nc [--tol 0.05]

Checks, in order (cheapest first):
    1. Byte identity (fast path; if identical, PASS immediately).
    2. Move-by-move G0/G1 XYZ equivalence within tolerance.
    3. Torch-event (deposition on/off) sequencing per move.
    4. Summary stats (move counts, layer count, deposition length, bbox).

Exit codes: 0 = pass, 2 = fail, 1 = usage/IO error.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from Waam_tech.nc import parser as nc_parser  # noqa: E402

_LAYER_RE = re.compile(r"^\(--- LAYER (\d+) Z=([-+0-9.]+) ---\)")


def _layer_markers(text: str) -> list[int]:
    layers = []
    for line in text.splitlines():
        match = _LAYER_RE.match(line.strip())
        if match:
            layers.append(int(match.group(1)))
    return layers


def _deposition_length(depo_moves) -> float:
    total = 0.0
    for start, end in depo_moves:
        total += math.dist(start, end)
    return total


def _bbox(points) -> tuple[float, float, float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def compare_nc_files(candidate_path: Path, reference_path: Path, *, tol: float = 0.05) -> list[str]:
    """Return a list of problems; empty list means the files are equivalent."""

    candidate_bytes = candidate_path.read_bytes()
    reference_bytes = reference_path.read_bytes()
    if candidate_bytes == reference_bytes:
        return []

    problems: list[str] = []

    candidate_text = candidate_bytes.decode("utf-8", errors="ignore")
    reference_text = reference_bytes.decode("utf-8", errors="ignore")

    cand_points, cand_depo = nc_parser.parse_nc_text(candidate_text, allow_config=False)
    ref_points, ref_depo = nc_parser.parse_nc_text(reference_text, allow_config=False)

    if len(cand_points) != len(ref_points):
        problems.append(
            f"Move count differs: candidate={len(cand_points)} reference={len(ref_points)}"
        )
    else:
        max_delta = 0.0
        worst_idx = -1
        for idx, (cp, rp) in enumerate(zip(cand_points, ref_points)):
            delta = math.dist(cp, rp)
            if delta > max_delta:
                max_delta = delta
                worst_idx = idx
        if max_delta > tol:
            problems.append(
                f"Move {worst_idx} XYZ differs by {max_delta:.4f}mm (tolerance {tol}mm)"
            )

        if list(cand_depo) != list(ref_depo):
            first_mismatch = next(
                (i for i, (c, r) in enumerate(zip(cand_depo, ref_depo)) if c != r), None
            )
            problems.append(
                f"Torch-on/off (deposition) sequencing differs starting at move {first_mismatch}"
            )

    cand_layers = _layer_markers(candidate_text)
    ref_layers = _layer_markers(reference_text)
    if cand_layers != ref_layers:
        problems.append(
            f"Layer sequence differs: candidate has {len(cand_layers)} layers, "
            f"reference has {len(ref_layers)} layers"
        )

    _, cand_depo_moves = nc_parser.split_moves(cand_points, cand_depo)
    _, ref_depo_moves = nc_parser.split_moves(ref_points, ref_depo)
    cand_len = _deposition_length(cand_depo_moves)
    ref_len = _deposition_length(ref_depo_moves)
    if ref_len > 0 and abs(cand_len - ref_len) / ref_len > 0.02:
        problems.append(
            f"Total deposition path length differs by "
            f"{abs(cand_len - ref_len) / ref_len * 100:.1f}% "
            f"(candidate={cand_len:.1f}mm reference={ref_len:.1f}mm)"
        )

    if len(cand_points) and len(ref_points):
        cand_bbox = _bbox(cand_points)
        ref_bbox = _bbox(ref_points)
        bbox_delta = max(abs(c - r) for c, r in zip(cand_bbox, ref_bbox))
        if bbox_delta > tol:
            problems.append(
                f"Toolpath bounding box differs by up to {bbox_delta:.4f}mm "
                f"(candidate={cand_bbox} reference={ref_bbox})"
            )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", help="Candidate NC file to check.")
    parser.add_argument("reference", help="Reference NC file to compare against.")
    parser.add_argument("--tol", type=float, default=0.05, help="Position tolerance in mm.")
    args = parser.parse_args(argv)

    candidate_path = Path(args.candidate)
    reference_path = Path(args.reference)
    if not candidate_path.is_file():
        print(f"[ERROR] Candidate NC not found: {candidate_path}")
        return 1
    if not reference_path.is_file():
        print(f"[ERROR] Reference NC not found: {reference_path}")
        return 1

    try:
        problems = compare_nc_files(candidate_path, reference_path, tol=args.tol)
    except Exception as exc:
        print(f"[ERROR] Comparison failed: {exc}")
        return 1

    if not problems:
        print(f"[PASS] {candidate_path.name} matches {reference_path.name}")
        return 0

    print(f"[FAIL] {candidate_path.name} differs from {reference_path.name}:")
    for problem in problems:
        print(f" - {problem}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
