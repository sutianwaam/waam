<p align="center">
  <img src="Waam_tech/icons/waam.svg" alt="WAAM" width="220">
</p>

<div align="center">

![FreeCAD](https://img.shields.io/badge/FreeCAD-0.21%2B-blue)
![Workflow](https://img.shields.io/badge/Workflow-Deterministic-0ea5e9)
![Mode](https://img.shields.io/badge/Mode-Offline-22c55e)
![Language](https://img.shields.io/badge/Language-Python-3776AB)

</div>

<details>
  <summary><h2>Table of Contents</h2></summary>

  <br/>

  - [Overview](#overview)
  - [Key Features](#key-features)
  - [Data Flow](#data-flow)
  - [Code Walkthrough](#code-walkthrough)
  - [Outputs](#outputs)
  - [Getting Started](#getting-started)
  - [GUI Workflow](#gui-workflow)
  - [Headless Usage](#headless-usage)
  - [NC Generation Modes](#nc-generation-modes)
  - [Torch Coverage and Voxel Tools](#torch-coverage-and-voxel-tools)
  - [Configuration](#configuration)
  - [Repository Layout](#repository-layout)
  - [Known Pitfalls](#known-pitfalls)
  - [Contributing and License](#contributing-and-license)
  - [Credits](#credits)

</details>

---

## Overview

FreeCAD WAAM Workbench provides deterministic, offline WAAM (Wire Arc Additive Manufacturing) tooling for FreeCAD. The workbench lets you set a WCS, extract WAAM-aware features, plan operations, fuse mesh and WAAM DSL context, preview toolpaths, and generate NC output.

## Key Features

- WCS marker and deposition start point stored in the FreeCAD document and propagated into features, planning, and NC.
- WCS-aligned feature extraction with mesh-aware risk tags (overhangs, thin walls).
- Deterministic operation planner with stable ordering, layer buckets, and tool selection.
- Baseline NC generator with contour/serpentine infill and support trimming.
- Mesh and DSL fusion to a single assistant-ready JSON.
- Iterative waamgen pipeline with validation, optional geometry patching, and coverage gating.
- **STEP to WAAM DSL conversion**: Built-in slicer to generate WAAM layers directly from STEP files.
- **Validation Tab**: Inspect layer statistics and visualize 2D contours before generation.
- Optional local LLM workflows (Ollama) for NC regeneration and refinement.

## Data Flow

FreeCAD model
-> `features.json`
-> `waam_operations.json`
-> baseline NC (`waam_baseline.nc`)

Optional:
`waam_operations.json` + mesh + WAAM DSL
-> `waam_operations_with_mesh.json`
-> waamgen (structured paths/layers)
-> NC

## Code Walkthrough

This section maps the source tree to the runtime behavior so you can find the right file quickly when debugging or extending the workbench.

### Workbench bootstrap

`InitGui.py` ensures the module is importable and registers the WAAM workbench. `Waam_tech/wb.py` defines the workbench itself and wires the toolbar/menu to `Waam_tech/ui/commands.py`. The main deterministic workflow (Features, Planning, Fusion) is orchestrated by `Waam_tech/ui/ui_workflow.py`. The validation tab logic resides in `Waam_tech/ui/ui_validator.py`.

### Configuration and paths

`Waam_tech/core/config_loader.py` locates `standard_waam.json` (env overrides, FreeCAD paths, repo default) and validates required post and deposition fields. `Waam_tech/core/paths.py` resolves output directories and artifact paths with environment overrides. `Waam_tech/core/path_compat.py` bridges FreeCAD Path API differences (SetupSheet placement, stock disablement).

### WCS and deposition start

`Waam_tech/ui/wcs_marker.py` stores a `WAAM_WCS` group with a placement, label (e.g., G54), and an optional deposition start point. It also draws colored axis lines so the WCS is visible in the viewport.

### Feature extraction (`features.json`)

`Waam_tech/part_features/feature_extract.py` resolves the WCS (stored marker → Path Job → identity), transforms the part into WCS, and emits:
- Cylindrical faces → `cylindrical_hole` or `through_hole`.
- Horizontal planar faces → `planar_pocket_floor` with optional loops.
- Remaining freeform faces → `freeform_area` bbox.

It adds `deposition_start` when present and attaches mesh-derived `risk_tags`. Mesh analysis lives in `Waam_tech/analysis/mesh_features.py`, which tessellates the shape (MeshPart when available) and reports global stats, principal axes, overhang/thin-wall samples, and curvature summaries.

### Deterministic planning (`waam_operations.json`)

`Waam_tech/operations/deterministic_planner.py` turns extracted features into a stable operation sequence. It can auto-scale process parameters based on part span (`process.auto_scale`), filters tiny planar faces based on bead width, generates rough/finish/drill ops, and groups them into layer buckets. The output contains `tools`, `sequence`, `feature_index`, `layers`, `process_parameters`, and metadata such as WCS, clearance, mesh strategy, and optional deposition start.

`Waam_tech/operations/spiral_stc.py` implements spiral spanning-tree coverage used when infill is injected during fusion.

### Baseline NC generation (bbox-based)

`Waam_tech/nc/nc_generator.py` reads `waam_operations.json` and produces a geometry-aware baseline NC program. It builds contour + serpentine hatch paths within feature bounding boxes, alternates hatch angles per layer, adds lead-in/out ramps, and honors the deposition start point when available. Support trimming is handled by `Waam_tech/nc/support_trim.py`, which removes unsupported segments based on bead width/overhang constraints. Torch/gas/wire commands come from the `post` block in the metadata.

### Fusion + waamgen pipeline

`Waam_tech/nc/fuse_ops_mesh.py` builds `waam_operations_with_mesh.json` by combining features, operations, config, mesh context, and WAAM DSL text. It parses PATHLIB data from `.llm_waam`, injects hatch or spiral infill, and computes frame transforms between CAD/WCS/mesh.

`Waam_tech/waamgen/pipeline.py` reduces the fused JSON to a light job representation, applies frame transforms, trims unsupported paths, generates G-code, and validates syntax/safety/geometry. Iterative regeneration, plotting (`Waam_tech/waamgen/viz.py`), and coverage gating are supported. The repo-root `waamgen.py` is a thin CLI wrapper.

### LLM + WAAM DSL helpers

`Waam_tech/llm_agent/` builds prompt payloads for local LLM workflows. `Waam_tech/llm_agent/step_to_waam_dsl.py` provides a FreeCAD Part-based slicer to convert STEP files directly to WAAM DSL. `Waam_tech/nc/waam_dsl_nc.py` parses `.waam` / `.llm_waam` text and emits NC directly.

### Mesh export and LLM bundles

`Waam_tech/mesh_export/obj_to_llm.py` converts OBJ meshes into compact `mesh_for_llm` text; `render_obj.py` renders snapshots; `manifest.py` builds a JSON bundle that groups mesh + WAAM context for assistants.

### Voxel + torch coverage

`Waam_tech/analysis/voxel_matrix.py` builds a WCS-aligned voxel grid and optional RLE payloads. `Waam_tech/analysis/torch_coverage.py` parses NC (via `Waam_tech/nc/parser.py`), voxelizes torch coverage, and reports missing/extra material coverage.

### Headless/CLI entry points

`Waam_tech/cli.py` drives feature extraction and planning from `freecadcmd`. `waamgen.py` wraps the waamgen generate/validate CLI.

## Outputs

Default outputs are stored under `Waam_tech/output/`:

- `features/features.json`: WCS-aligned feature extraction and mesh analysis summary.
- `operations/waam_operations.json`: deterministic planner output and process parameters.
- `operations_mesh/waam_operations_with_mesh.json`: fused operations, config, features, mesh, and WAAM DSL.
- `mesh_exports/*.mesh_for_llm`: compact mesh summaries for assistants.
- `dsl_waam/*.llm_waam`: WAAM DSL text.
- `nc_files/*.nc`: baseline or final NC outputs.
- `waamgen_attempts/attempt_*.nc`: waamgen iterations.
- `voxel_matrix/*.rle.json`, `torch_coverage/*.json`: voxel and coverage payloads.

Generated artifacts are outputs; avoid editing them unless you are intentionally modifying output files.

## Getting Started

### Prerequisites

- FreeCAD 0.21+ (PySide2/6). Optional: matplotlib and numpy for the torch path viewer.
- If running outside FreeCAD, use the Python dependencies in `requirements.txt`.
- Optional for local LLM workflows: install Ollama and your preferred models.

### Installation

Place this repo in your FreeCAD `Mod/` folder (for example `.../FreeCAD/Mod/WAAM`).

## GUI Workflow

1) WAAM: Set WCS (stores WCS and optional deposition start in the document).
2) WAAM: Extract Features -> `output/features/features.json`.
3) WAAM: Plan Operations -> `output/operations/waam_operations.json`.
4) WAAM: NC -> `output/nc_files/waam_baseline.nc` (or waamgen loop if enabled).
5) Optional: Validate -> Check layer stats and contours in the Validator tab.
6) Optional: Fuse Plan + Mesh -> `output/operations_mesh/waam_operations_with_mesh.json`.
7) Optional: Torch Viewer / Assistant / Coverage / Voxel tools.

## Headless Usage

```bash
freecadcmd -c "import Waam_tech.cli; Waam_tech.cli.main(['path/to/model.FCStd'])"
```

Common flags: `--features-only`, `--plan-only`, `--config`, `--output-dir`, `--wcs-name`, `--keep-open`.

## NC Generation Modes

- Baseline (`Waam_tech/nc/nc_generator.py`): bbox-based contour + serpentine hatch, lead-in/out, optional chunking. Source: `waam_operations.json`.
- waamgen (`Waam_tech/waamgen/pipeline.py`): uses `llm_context.waam_structured` paths/layers in the fused JSON, builds IR to G-code, validates syntax/safety/geometry, saves `waamgen_attempts/attempt_*.nc`, writes final NC when a pass occurs. CLI: `python waamgen.py generate/validate`.
- WAAM DSL (`Waam_tech/nc/waam_dsl_nc.py`): converts `.waam`/`.llm_waam` text to NC.

Example waamgen usage:

```bash
python waamgen.py generate --input output/operations_mesh/waam_operations_with_mesh.json --out output/nc_files/waamgen.nc
python waamgen.py validate --gcode output/nc_files/waamgen.nc --ref output/operations_mesh/waam_operations_with_mesh.json
```

## NC Edit Agent (Local LLM)

Use the Ollama client to iteratively edit existing NC programs with a local model (set via `WAAM_NC_MODEL`).

```bash
python -m Waam_tech.llm_agent.ollama_client \
  --mode edit \
  --input-nc output/nc_files/waam_baseline.nc \
  --prompt "Reduce travel speed by 20% and add a 2s dwell before each torch-off." \
  --out-dir output/nc_files/nc_agent \
  --history output/nc_files/nc_agent/session.json
```

This writes incremented outputs like `waam_baseline_agent_001.nc`, `waam_baseline_agent_002.nc`, etc.

For context-aware refinement, use `--mode refine` and pass `--json` (WAAM JSON) plus `--mesh` (mesh_for_llm.txt or OBJ).

## Torch Coverage and Voxel Tools

- `Waam_tech/analysis/voxel_matrix.py` builds voxel grids (and RLE payloads) of the part in WCS.
- `Waam_tech/analysis/torch_coverage.py` compares torch coverage against material voxels.
- GUI dialogs are available for both and write outputs under `output/voxel_matrix/` and `output/torch_coverage/`.

## Configuration

- Default config: `Waam_tech/config/standard_waam.json`.
- Override with `WAAM_CONFIG_PATH` (or `Waam_CONFIG_PATH`).
- Output directory can be overridden via `WAAM_OUTPUT_DIR` (or `Waam_OUTPUT_DIR`).
- LLM defaults use `WAAM_PLANNER_MODEL` and `WAAM_NC_MODEL` for Ollama models.
- Optional: `process.auto_scale` lets you scale deposition/path parameters by part max span (see config for ranges).

## Repository Layout

```
WAAM/
- Init.py, InitGui.py
- waamgen.py            # CLI wrapper for waamgen generate/validate
- Waam_tech/
  - core/               # config and path helpers
  - part_features/      # feature extraction and mesh analysis
  - operations/         # deterministic planner
  - nc/                 # baseline NC, DSL converter, fusion helper
  - waamgen/            # IR to G-code and validation
  - llm_agent/          # assistant context and Ollama integration
  - mesh_export/        # mesh and torch coverage exports
  - ui/                 # dialogs, commands, viewer
  - output/             # default artifact root
- requirements.txt
```

## Known Pitfalls

- Support trimming can remove interior paths on step features. Review `Waam_tech/waamgen/pipeline.py` and `Waam_tech/nc/support_trim.py` if paths vanish.
- The baseline NC generator uses feature bounding boxes, not full geometry; thin features smaller than the bead width can collapse.
- waamgen requires `llm_context.waam_structured` in the fused JSON.

## Development Notes

- **Feature extraction**: Extend classification rules or add new feature types in `Waam_tech/part_features/feature_extract.py`. Mesh-derived `risk_tags` are produced in `Waam_tech/part_features/mesh_analysis.py`.
- **Deterministic planner**: Tool selection, sequencing, and layer bucketing live in `Waam_tech/operations/deterministic_planner.py`. Keep behavior deterministic and stable between runs.
- **NC generation**: Contour/serpentine infill, lead-in/out strategy, and post mapping are in `Waam_tech/nc/nc_generator.py`. Support trimming thresholds are in `Waam_tech/nc/support_trim.py`.
- **waamgen pipeline**: The light job IR, validation rules, and coverage gating are implemented in `Waam_tech/waamgen/pipeline.py`. This is the place to add more checks or alternative G-code emission strategies.
- **Configuration**: Process, machine, and post parameters come from `Waam_tech/config/standard_waam.json` (overridable via `WAAM_CONFIG_PATH`). Output directories and overrides are handled in `Waam_tech/core/paths.py`.
- **LLM workflows**: Prompt templates and orchestration live in `Waam_tech/llm_agent/llm_pipeline.py`, with context assembly in `Waam_tech/llm_agent/context_builder.py`. These can be customized without touching the deterministic pipeline.

## Contributing and License

Contributions and issues are welcome. License is not specified in this repo; add a LICENSE file if you want to formalize it.

## Credits

- Built on FreeCAD.

---
