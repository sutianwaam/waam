"""Regression coverage for the INFILL -> LINK -> INFILL continuity feature.

`slice_plan._append_hatch_snake_paths` emits a `LINK` (skip_deposition)
path across gaps in a hatch-only snake pass (e.g. crossing a void), and
`waamgen/pipeline.py`'s `_layer_has_skip_paths` / `_emit_ordered_path_sequence`
consume it: torch off for the link, travel, torch on again for the next
pass. This was previously verified only by hand with a one-off synthetic
plan; this test makes that check permanent and automated so a future change
to either side of the LINK contract gets caught.
"""

from __future__ import annotations

import json

from Waam_tech.waamgen.pipeline import build_ir, ir_to_gcode, load_light_job, validate_gcode

VALID_POST = {
    "torch_on": "M62 P0",
    "torch_off": "M63 P0",
    "gas_on": "M64 P0",
    "gas_off": "M65 P0",
    "wire_feed": "M68 E0 Q{wire_feed}",
    "travel_speed": "F{travel_speed}",
}

VALID_DEPOSITION = {
    "bead_width_mm": 6.0,
    "layer_height_mm": 1.5,
    "travel_speed_mm_min": 320.0,
    "wire_feed_mm_min": 3000.0,
    "dwell_per_layer_s": 1.5,
}


def _synthetic_link_plan() -> dict:
    return {
        "metadata": {"units": "mm", "wcs": "G54"},
        "process": {"deposition": VALID_DEPOSITION},
        "process_parameters": {"deposition": VALID_DEPOSITION},
        "post": VALID_POST,
        "llm_context": {
            "waam_structured": {
                "paths": {
                    "P0": {
                        "id": "P0",
                        "role_hint": "INFILL",
                        "frame": "wcs_frame",
                        "closed": False,
                        "points_xyz": [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]],
                        "planner_tags": {"region_id": "L0_R0", "continuity_reversible": True},
                    },
                    "LINK0": {
                        "id": "LINK0",
                        "role_hint": "LINK",
                        "frame": "wcs_frame",
                        "closed": False,
                        "points_xyz": [[10.0, 0.0, 0.0], [10.0, 5.0, 0.0]],
                        "planner_tags": {
                            "region_id": "L0_R0",
                            "skip_deposition": True,
                            "continuity_reversible": False,
                        },
                    },
                    "P1": {
                        "id": "P1",
                        "role_hint": "INFILL",
                        "frame": "wcs_frame",
                        "closed": False,
                        "points_xyz": [[10.0, 5.0, 0.0], [20.0, 5.0, 0.0]],
                        "planner_tags": {"region_id": "L0_R0", "continuity_reversible": True},
                    },
                },
                "layers": [
                    {"i": 0, "z": 1.5, "path_ids": ["P0", "LINK0", "P1"]},
                ],
            }
        },
    }


def test_link_path_turns_torch_off_and_back_on(tmp_path):
    plan_path = tmp_path / "link_plan.json"
    plan_path.write_text(json.dumps(_synthetic_link_plan()), encoding="utf-8")

    job = load_light_job(plan_path)
    ir = build_ir(job)
    nc_text = ir_to_gcode(ir)

    lines = [line.strip() for line in nc_text.splitlines()]
    torch_on_idx = [i for i, line in enumerate(lines) if line.startswith("M62")]
    torch_off_idx = [i for i, line in enumerate(lines) if line.startswith("M63")]
    assert len(torch_on_idx) == 2, "expected two deposition passes (P0 and P1) to each turn the torch on"
    assert len(torch_off_idx) >= 2, "expected at least one torch-off for the LINK crossing plus end-of-program"
    # The LINK crossing must turn the torch off before the second pass turns it back on.
    assert torch_off_idx[0] < torch_on_idx[1]

    report = validate_gcode(nc_text, job, geom_tol=0.8)
    assert report.syntax_ok, report.messages
    assert report.safety_ok, report.messages
    assert report.geometry_ok, report.messages
    assert report.ok


def test_link_path_never_marked_as_deposition_move(tmp_path):
    from Waam_tech.nc import parser as nc_parser

    plan_path = tmp_path / "link_plan.json"
    plan_path.write_text(json.dumps(_synthetic_link_plan()), encoding="utf-8")

    job = load_light_job(plan_path)
    nc_text = ir_to_gcode(build_ir(job))

    points, deposition = nc_parser.parse_nc_text(nc_text, allow_config=False)
    _travel, depo_moves = nc_parser.split_moves(points, deposition)
    # No deposition move should cross through the LINK gap region (x=10, 0<y<5).
    for start, end in depo_moves:
        if abs(start[0] - 10.0) < 1e-6 and abs(end[0] - 10.0) < 1e-6:
            assert not (0.0 < start[1] < 5.0 or 0.0 < end[1] < 5.0), (
                "a deposition move was found inside the LINK (void) crossing"
            )
