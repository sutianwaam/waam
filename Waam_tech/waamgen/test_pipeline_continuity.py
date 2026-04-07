from __future__ import annotations

import json
import tempfile
from pathlib import Path

from Waam_tech.waamgen.pipeline import (
    _build_layer_path_chains,
    build_ir,
    ir_to_gcode,
    load_light_job,
)


def _continuity_cfg(*, emit_chains: bool = True, max_gap: float = 0.8) -> dict:
    return {
        "enabled": True,
        "rotate_closed_paths": True,
        "allow_reverse_open_paths": True,
        "restart_penalty_mm": 20.0,
        "heading_change_penalty_mm": 4.0,
        "region_switch_penalty_mm": 3.0,
        "max_chain_gap_mm": max_gap,
        "emit_chains": emit_chains,
        "allowed_roles": ["INFILL"],
        "allowed_region_modes": ["bulk_fill"],
        "disallow_hotspot_kinds": ["corner", "junction"],
        "max_chain_heading_delta_deg": 60.0,
        "max_paths_per_chain": 12,
        "coincident_endpoint_tol_mm": 0.05,
    }


def _minimal_ir(*, emit_chains: bool = True, gap: float = 0.5) -> dict:
    return {
        "meta": {
            "units": "mm",
            "wcs": {"name": "G54"},
            "bead_width_mm": 2.0,
        },
        "metadata": {
            "units": "mm",
            "wcs": {"name": "G54"},
            "clearance": 10.0,
            "rapidZ": 10.0,
        },
        "process": {
            "travel_speed_mm_min": 300.0,
            "wire_feed_mm_min": 1000.0,
            "dwell_per_layer_s": 0.1,
        },
        "process_config": {
            "path": {
                "topology_sensitive": {
                    "continuity": _continuity_cfg(emit_chains=emit_chains, max_gap=0.8),
                }
            }
        },
        "post": {
            "torch_on": "M62 P0",
            "torch_off": "M63 P0",
            "gas_on": "M64 P0",
            "gas_off": "M65 P0",
            "wire_feed": "M68 E0 Q{wire_feed:.1f}",
            "travel_speed": "F{travel_speed:.1f}",
        },
        "post_meta": {
            "arc_start_delay_s": 0.0,
            "arc_stop_delay_s": 0.0,
            "gas_preflow_s": 0.0,
            "gas_postflow_s": 0.0,
        },
        "paths": {
            "P1": [[0.0, 0.0], [5.0, 0.0]],
            "P2": [[5.0 + gap, 0.0], [10.0, 0.0]],
        },
        "path_meta": {
            "P1": {
                "id": "P1",
                "role_hint": "INFILL",
                "closed": False,
                "planner_tags": {
                    "region_id": "R0",
                    "region_mode": "bulk_fill",
                    "hotspot_kinds": [],
                    "continuity_reversible": True,
                },
            },
            "P2": {
                "id": "P2",
                "role_hint": "INFILL",
                "closed": False,
                "planner_tags": {
                    "region_id": "R0",
                    "region_mode": "bulk_fill",
                    "hotspot_kinds": [],
                    "continuity_reversible": True,
                },
            },
        },
        "layers": [
            {
                "index": 0,
                "z": 0.0,
                "path_ids": ["P1", "P2"],
                "meta": {
                    "planner_mode": "layered_topology",
                    "topology": {
                        "continuity": {
                            "ordered_path_ids": ["P1", "P2"],
                        }
                    },
                },
            }
        ],
    }


def _write_job_json(root: Path) -> Path:
    payload = {
        "metadata": {
            "units": "mm",
            "wcs": {"name": "G54"},
            "clearance": 10.0,
            "rapidZ": 10.0,
        },
        "process_parameters": {
            "deposition": {
                "bead_width_mm": 2.0,
                "layer_height_mm": 1.0,
                "travel_speed_mm_min": 300.0,
                "wire_feed_mm_min": 1000.0,
                "dwell_per_layer_s": 0.1,
            },
            "path": {
                "topology_sensitive": {
                    "continuity": _continuity_cfg(emit_chains=True),
                }
            },
        },
        "post": {
            "torch_on": "M62 P0",
            "torch_off": "M63 P0",
            "gas_on": "M64 P0",
            "gas_off": "M65 P0",
            "wire_feed": "M68 E0 Q{wire_feed:.1f}",
            "travel_speed": "F{travel_speed:.1f}",
        },
        "llm_context": {
            "waam_structured": {
                "frames": {},
                "paths": {
                    "P1": {
                        "id": "P1",
                        "role_hint": "INFILL",
                        "frame": "mesh_frame",
                        "closed": False,
                        "planner_tags": {
                            "region_id": "R0",
                            "region_mode": "bulk_fill",
                            "hotspot_kinds": [],
                            "continuity_reversible": True,
                        },
                        "points_xyz": [[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]],
                    },
                    "P2": {
                        "id": "P2",
                        "role_hint": "INFILL",
                        "frame": "mesh_frame",
                        "closed": False,
                        "planner_tags": {
                            "region_id": "R0",
                            "region_mode": "bulk_fill",
                            "hotspot_kinds": [],
                            "continuity_reversible": True,
                        },
                        "points_xyz": [[5.5, 0.0, 0.0], [10.0, 0.0, 0.0]],
                    },
                },
                "layers": [
                    {
                        "i": 0,
                        "z": 0.0,
                        "path_ids": ["P1", "P2"],
                        "planner_mode": "layered_topology",
                        "topology": {
                            "continuity": {
                                "ordered_path_ids": ["P1", "P2"],
                                "estimated_restarts": 1,
                            }
                        },
                    }
                ],
            }
        },
    }
    path = root / "job.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_out_of_order_job_json(root: Path) -> Path:
    payload = {
        "metadata": {
            "units": "mm",
            "wcs": {"name": "G54"},
            "clearance": 10.0,
            "rapidZ": 10.0,
        },
        "process_parameters": {
            "deposition": {
                "bead_width_mm": 2.0,
                "layer_height_mm": 1.0,
                "travel_speed_mm_min": 300.0,
                "wire_feed_mm_min": 1000.0,
                "dwell_per_layer_s": 0.1,
            },
            "path": {
                "topology_sensitive": {
                    "continuity": _continuity_cfg(emit_chains=False),
                }
            },
        },
        "post": {
            "torch_on": "M62 P0",
            "torch_off": "M63 P0",
            "gas_on": "M64 P0",
            "gas_off": "M65 P0",
            "wire_feed": "M68 E0 Q{wire_feed:.1f}",
            "travel_speed": "F{travel_speed:.1f}",
        },
        "llm_context": {
            "waam_structured": {
                "frames": {},
                "paths": {
                    "P_TOP": {"id": "P_TOP", "role_hint": "INFILL", "frame": "mesh_frame", "closed": False, "points_xyz": [[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]]},
                    "P_BASE": {"id": "P_BASE", "role_hint": "INFILL", "frame": "mesh_frame", "closed": False, "points_xyz": [[0.0, 1.0, 0.0], [5.0, 1.0, 0.0]]},
                    "P_MID": {"id": "P_MID", "role_hint": "INFILL", "frame": "mesh_frame", "closed": False, "points_xyz": [[0.0, 2.0, 0.0], [5.0, 2.0, 0.0]]},
                },
                "layers": [
                    {"i": 9, "z": 2.0, "path_ids": ["P_TOP"]},
                    {"i": 3, "z": 0.0, "path_ids": ["P_BASE"]},
                    {"i": 6, "z": 1.0, "path_ids": ["P_MID"]},
                ],
            }
        },
    }
    path = root / "job_out_of_order.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _count_prefix(text: str, prefix: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip().startswith(prefix))


def _assert_no_g0_while_torch_on(gcode_text: str, *, on_code: str = "M62 P0", off_code: str = "M63 P0") -> None:
    torch_on = False
    for raw in gcode_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(on_code):
            torch_on = True
            continue
        if line.startswith(off_code):
            torch_on = False
            continue
        if torch_on:
            assert not line.startswith("G0"), f"Rapid move emitted while torch on: {line}"


def test_load_light_job_preserves_planner_tags() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        job_path = _write_job_json(Path(tmpdir))
        job = load_light_job(job_path)
        ir = build_ir(job)

    assert job.path_meta["P1"]["planner_tags"]["region_id"] == "R0"
    assert job.layers[0].meta["topology"]["continuity"]["ordered_path_ids"] == ["P1", "P2"]
    assert ir["path_meta"]["P2"]["planner_tags"]["region_mode"] == "bulk_fill"


def test_load_light_job_sorts_layers_in_strict_z_order_without_start_point() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        job_path = _write_out_of_order_job_json(Path(tmpdir))
        job = load_light_job(job_path)
        gcode = ir_to_gcode(build_ir(job))

    assert [layer.z for layer in job.layers] == [0.0, 1.0, 2.0]
    assert [layer.index for layer in job.layers] == [0, 1, 2]
    assert [
        line for line in gcode.splitlines() if line.startswith("(--- LAYER")
    ] == [
        "(--- LAYER 0 Z=0.000 ---)",
        "(--- LAYER 1 Z=1.000 ---)",
        "(--- LAYER 2 Z=2.000 ---)",
    ]


def test_build_layer_path_chains_groups_compatible_infill_paths() -> None:
    ir = _minimal_ir(emit_chains=True, gap=0.5)
    chains = _build_layer_path_chains(
        ir["layers"][0],
        paths=ir["paths"],
        path_meta=ir["path_meta"],
        continuity_cfg=ir["process_config"]["path"]["topology_sensitive"]["continuity"],
    )

    assert chains == [["P1", "P2"]]


def test_build_layer_path_chains_rejects_large_gap() -> None:
    ir = _minimal_ir(emit_chains=True, gap=1.4)
    chains = _build_layer_path_chains(
        ir["layers"][0],
        paths=ir["paths"],
        path_meta=ir["path_meta"],
        continuity_cfg=ir["process_config"]["path"]["topology_sensitive"]["continuity"],
    )

    assert chains == [["P1"], ["P2"]]


def test_build_layer_path_chains_rejects_hotspot_paths() -> None:
    ir = _minimal_ir(emit_chains=True, gap=0.5)
    ir["path_meta"]["P2"]["planner_tags"]["hotspot_kinds"] = ["corner"]
    chains = _build_layer_path_chains(
        ir["layers"][0],
        paths=ir["paths"],
        path_meta=ir["path_meta"],
        continuity_cfg=ir["process_config"]["path"]["topology_sensitive"]["continuity"],
    )

    assert chains == [["P1"], ["P2"]]


def test_build_layer_path_chains_rejects_different_region_ids() -> None:
    ir = _minimal_ir(emit_chains=True, gap=0.5)
    ir["path_meta"]["P2"]["planner_tags"]["region_id"] = "R1"
    chains = _build_layer_path_chains(
        ir["layers"][0],
        paths=ir["paths"],
        path_meta=ir["path_meta"],
        continuity_cfg=ir["process_config"]["path"]["topology_sensitive"]["continuity"],
    )

    assert chains == [["P1"], ["P2"]]


def test_ir_to_gcode_emits_single_torch_cycle_for_chain() -> None:
    ir = _minimal_ir(emit_chains=True, gap=0.5)
    gcode = ir_to_gcode(ir)

    assert _count_prefix(gcode, "M62 P0") == 1
    assert "(Chain P1 -> P2)" in gcode
    assert "G1 X5.500 Y0.000" in gcode


def test_ir_to_gcode_falls_back_when_emit_chains_disabled() -> None:
    ir = _minimal_ir(emit_chains=False, gap=0.5)
    gcode = ir_to_gcode(ir)

    assert _count_prefix(gcode, "M62 P0") == 2
    assert "(Chain P1 -> P2)" not in gcode


def test_ir_to_gcode_never_rapid_with_torch_on_in_chain_mode() -> None:
    ir = _minimal_ir(emit_chains=True, gap=0.5)
    gcode = ir_to_gcode(ir)

    _assert_no_g0_while_torch_on(gcode)
