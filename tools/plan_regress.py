#!/usr/bin/env python3
"""Semantically diff two waam_slice_plan.json files (checkpoint A/B).

This catches upstream (FreeCAD-side) planner deviations before any NC is
built: layer counts, per-layer Z, path counts, path geometry (within
tolerance), and process parameters. Volatile metadata (timestamps, absolute
paths) is ignored on purpose.

Usage:
    python tools/plan_regress.py candidate.json reference.json [--tol 0.05]

Exit codes: 0 = pass, 2 = semantic differences found, 1 = usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

_PROCESS_KEYS_OF_INTEREST = (
    ("deposition", "bead_width_mm"),
    ("deposition", "layer_height_mm"),
    ("deposition", "overlap_pct"),
    ("deposition", "strategy"),
    ("path", "deposition_pattern"),
    ("path", "hatch_angle_deg"),
    ("path", "hatch_angle_step_deg"),
    ("path", "planner_mode"),
)


def _load(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _structured(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    ctx = plan.get("llm_context") or {}
    return ctx.get("waam_structured") or ctx.get("toolpath") or {}


def _layers_by_index(structured: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    out: dict[int, Mapping[str, Any]] = {}
    for item in structured.get("layers") or []:
        if not isinstance(item, Mapping):
            continue
        idx = int(item.get("i", item.get("index", 0)))
        out[idx] = item
    return out


def _path_points(path_entry: Mapping[str, Any]) -> list[tuple[float, float, float]]:
    pts = path_entry.get("points_xyz") or path_entry.get("points") or []
    out = []
    for entry in pts:
        try:
            x, y = float(entry[0]), float(entry[1])
            z = float(entry[2]) if len(entry) >= 3 else 0.0
            out.append((x, y, z))
        except Exception:
            continue
    return out


def _get_nested(mapping: Mapping[str, Any], *keys: str, default=None):
    cur: Any = mapping
    for key in keys:
        if not isinstance(cur, Mapping):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def compare_plans(candidate: Mapping[str, Any], reference: Mapping[str, Any], *, tol: float = 0.05) -> list[str]:
    problems: list[str] = []

    cand_structured = _structured(candidate)
    ref_structured = _structured(reference)

    cand_layers = _layers_by_index(cand_structured)
    ref_layers = _layers_by_index(ref_structured)

    if len(cand_layers) != len(ref_layers):
        problems.append(f"Layer count differs: candidate={len(cand_layers)} reference={len(ref_layers)}")

    common_indices = sorted(set(cand_layers) & set(ref_layers))
    missing_in_cand = sorted(set(ref_layers) - set(cand_layers))
    missing_in_ref = sorted(set(cand_layers) - set(ref_layers))
    if missing_in_cand:
        problems.append(f"Layers missing in candidate: {missing_in_cand}")
    if missing_in_ref:
        problems.append(f"Layers missing in reference (new in candidate): {missing_in_ref}")

    for idx in common_indices:
        cand_layer = cand_layers[idx]
        ref_layer = ref_layers[idx]
        cand_z = float(cand_layer.get("z", cand_layer.get("z_mm", 0.0)))
        ref_z = float(ref_layer.get("z", ref_layer.get("z_mm", 0.0)))
        if abs(cand_z - ref_z) > tol:
            problems.append(f"Layer {idx} Z differs: candidate={cand_z:.3f} reference={ref_z:.3f}")

        cand_path_ids = list(cand_layer.get("path_ids") or [])
        ref_path_ids = list(ref_layer.get("path_ids") or [])
        if len(cand_path_ids) != len(ref_path_ids):
            problems.append(
                f"Layer {idx} path count differs: candidate={len(cand_path_ids)} reference={len(ref_path_ids)}"
            )

    cand_paths = cand_structured.get("paths") or {}
    ref_paths = ref_structured.get("paths") or {}
    if len(cand_paths) != len(ref_paths):
        problems.append(f"Total path count differs: candidate={len(cand_paths)} reference={len(ref_paths)}")

    shared_ids = sorted(set(cand_paths) & set(ref_paths))
    geometry_mismatches = 0
    worst_delta = 0.0
    for pid in shared_ids:
        cand_pts = _path_points(cand_paths[pid])
        ref_pts = _path_points(ref_paths[pid])
        if len(cand_pts) != len(ref_pts):
            geometry_mismatches += 1
            continue
        for cp, rp in zip(cand_pts, ref_pts):
            delta = math.dist(cp, rp)
            worst_delta = max(worst_delta, delta)
    if geometry_mismatches:
        problems.append(f"{geometry_mismatches} shared path(s) have a different point count")
    if worst_delta > tol:
        problems.append(f"Path geometry differs by up to {worst_delta:.4f}mm (tolerance {tol}mm)")

    cand_process = candidate.get("process_parameters") or candidate.get("process") or {}
    ref_process = reference.get("process_parameters") or reference.get("process") or {}
    for group, key in _PROCESS_KEYS_OF_INTEREST:
        cand_val = _get_nested(cand_process, group, key)
        ref_val = _get_nested(ref_process, group, key)
        if cand_val != ref_val:
            problems.append(f"process.{group}.{key} differs: candidate={cand_val!r} reference={ref_val!r}")

    cand_wcs = _get_nested(candidate, "metadata", "wcs", "name")
    ref_wcs = _get_nested(reference, "metadata", "wcs", "name")
    if cand_wcs != ref_wcs:
        problems.append(f"metadata.wcs.name differs: candidate={cand_wcs!r} reference={ref_wcs!r}")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", help="Candidate waam_slice_plan.json.")
    parser.add_argument("reference", help="Reference waam_slice_plan.json.")
    parser.add_argument("--tol", type=float, default=0.05, help="Geometry/Z tolerance in mm.")
    args = parser.parse_args(argv)

    candidate_path = Path(args.candidate)
    reference_path = Path(args.reference)
    if not candidate_path.is_file():
        print(f"[ERROR] Candidate plan not found: {candidate_path}")
        return 1
    if not reference_path.is_file():
        print(f"[ERROR] Reference plan not found: {reference_path}")
        return 1

    try:
        candidate = _load(candidate_path)
        reference = _load(reference_path)
        problems = compare_plans(candidate, reference, tol=args.tol)
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
