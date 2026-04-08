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
- `WAAM: Planner`
- `WAAM: Mesh Prep`
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
```

## Requirements

- `numpy`
- `matplotlib`
- `PySide2` or `PySide6` through the FreeCAD environment

Notes:

- `matplotlib` and `numpy` are required for the torch viewer.
- the workbench is designed to run offline
- `Waam_tech/config/standard_waam.json` is used even when it is not frequently edited, so it should be treated as a live runtime file
