<p align="center">
  <img src="Waam_tech/icons/waam.svg" alt="WAAM" width="220">
</p>

<div align="center">

![FreeCAD](https://img.shields.io/badge/FreeCAD-0.21%2B-blue)
![Workflow](https://img.shields.io/badge/Workflow-Slice--First-0ea5e9)
![Mode](https://img.shields.io/badge/Mode-Offline-22c55e)
![Language](https://img.shields.io/badge/Language-Python-3776AB)

</div>

<details>
  <summary><h2>Table of Contents</h2></summary>

  <br/>

  - [Overview](#overview)
  - [Key Features](#key-features)
  - [Data Flow](#data-flow)
  - [Getting Started](#getting-started)
  - [GUI Workflow](#gui-workflow)
  - [Outputs](#outputs)
  - [Experimental Runtime](#experimental-runtime)
  - [Pipeline Checkpoints](#pipeline-checkpoints)
  - [Testing](#testing)
  - [Repository Layout](#repository-layout)
  - [Requirements](#requirements)

</details>

---

## Overview

WAAM is a streamlined FreeCAD workbench focused on a simple WAAM production path:

1. set the WCS
2. export the STEP
3. create a slice-first WAAM plan
4. generate the minimal mesh and LLM artifacts when needed
5. create `waam_baseline.nc`
6. inspect the result in the torch viewer

The `WAAM` workbench is registered in FreeCAD. The legacy analysis stack and larger planning surfaces are intentionally removed from this streamlined build.

## Key Features

- WCS-driven workflow with a dedicated setup dialog
- slice-first planning in WCS-local `XY` layers
- simple deposition strategy based on real geometric sections
- compact NC generation path through `waamgen`
- optional compact `part.llm_waam` export for LLM-oriented workflows
- torch path visualization for both plan JSON and NC files
- experimental runtime sandbox kept available for project-specific iteration

## Data Flow

The primary execution flow is:

```text
FreeCAD model
-> waam_slice_plan.json
-> waam_baseline.nc
```

The normal pipeline may also write auxiliary artifacts:

```text
FreeCAD model
-> output/features/features.json
-> output/operations_mesh/waam_slice_plan.json
-> output/dsl_waam/part.llm_waam
-> output/nc_files/waam_baseline.nc
```

## Getting Started

1. Install this module inside your FreeCAD `Mod` directory.
2. Install the Python dependencies into the Python environment used by FreeCAD:

```bash
python -m pip install -r requirements.txt
```

3. Restart FreeCAD.
4. Open the `WAAM` workbench.

## GUI Workflow

The toolbar and menu are intentionally small:

- `WAAM: Set WCS`
- `WAAM: Export STEP`
- `WAAM: XYZ Point Cloud` (interior solid-grid points, custom spacing + CSV/XLSX export)
- `WAAM: Planner`
- `WAAM: NC Creator`
- `WAAM: Torch Viewer`

The fastest path is usually the `Run Full Pipeline` button inside the WCS dialog. That path exports the STEP, slices the visible solids in WCS-local `XY` layers, creates the slice plan, and generates the baseline NC in one run.

## Outputs

Important output paths:

- `Waam_tech/input_step/part.step`
- `Waam_tech/output/features/features.json`
- `Waam_tech/output/operations_mesh/waam_slice_plan.json`
- `Waam_tech/output/dsl_waam/part.llm_waam`
- `Waam_tech/output/nc_files/waam_baseline.nc`

The slice plan JSON is the main execution artifact. `features.json` is kept as metadata, and `part.llm_waam` is a compact derived artifact intended for LLM-oriented workflows.

## Experimental Runtime

The WCS dialog still includes `Run Pipeline + Experimental NC`.

That path keeps the stable baseline flow but also exports an experimental NC using the runtime sandbox under:

- `Waam_tech/experimental/runtime/active/`

This folder is intentionally preserved as the place to iterate on project-specific planner behavior without changing the baseline WAAM pipeline first.

## Pipeline Checkpoints

The slice-first pipeline is deterministic: the same part, the same WCS, and
the same config always produce the same trajectory. That is what makes it
possible to regression-test generated artifacts before physical deposition.
Three checkpoints make this traceable end to end:

```text
FreeCAD model
   -> [checkpoint A] waam_slice_plan.json + <plan>.manifest.json
        (model hash, WCS name/source/warnings, config hash,
         layer/path counts, deposition start + source)
   -> [checkpoint B] plan schema validation (waamgen.plan_schema)
        (required keys, WCS present, non-empty layers, monotonic Z;
         fails fast and names the stage that produced bad data)
   -> waam_baseline.nc / waam_baseline_experimental.nc
        (header comments carry source plan filename + hash,
         config hash, generator version)
   -> [checkpoint C] validate / regress
        (tools/waam_regress.py, tools/plan_regress.py, pytest tests/)
```

## Testing

The regression tests and tools run **without FreeCAD** (they only need the
`waamgen`/`nc`/`core`/`slicing`-adjacent pure-Python modules and a frozen
plan fixture):

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

- `tools/waam_regress.py candidate.nc reference.nc` -- compares two NC
  files: byte identity first, then move-by-move G0/G1 equivalence, torch-event
  sequencing, and summary stats within tolerance.
- `tools/plan_regress.py candidate.json reference.json` -- semantically
  diffs two slice plans (layers, paths, geometry, process parameters),
  ignoring volatile metadata like timestamps and absolute paths.
- Golden fixtures live in `tests/fixtures/golden/` and are frozen,
  known-good copies (not the same thing as the live, gitignored
  `Waam_tech/output/` artifacts, which are regenerated locally and are not
  committed).

See `AGENTS.md` for the full checkpoint diagram and the agent roles wired
to these commands.

## Repository Layout

```text
Init.py
InitGui.py
Waam_tech/
  config/           standard WAAM process defaults
  core/             path/config/WCS helpers
  experimental/     project-specific experimental planning area
  icons/            workbench icons
  input_step/       default STEP input/output location
  mesh_export/      OBJ mesh export helpers
  nc/               NC parsing and support path helpers
  output/           generated artifacts
  ui/               dialogs, commands, and torch viewer
  slicing/          slicing, section extraction, and compact WAAM exports
  waamgen/          NC generation pipeline
  wb.py             workbench registration
tests/              pytest suite (golden artifacts, unit tests; no FreeCAD needed)
tools/               waam_regress.py / plan_regress.py standalone regression CLIs
```

## Requirements

- `numpy`
- `matplotlib`
- `PySide2` or `PySide6` through the FreeCAD environment

Notes:

- `matplotlib` and `numpy` are required for the torch viewer.
- the workbench is designed to run offline
- `Waam_tech/config/standard_waam.json` is used even when it is not frequently edited, so it should be treated as a live runtime file
