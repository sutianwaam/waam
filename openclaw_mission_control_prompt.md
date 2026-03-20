# OpenClaw Prompt For WAAM Mission Control Panel

Use the repository's `mission_control.yml` as the main source of truth and build a real Mission Control panel for this project.

## Prompt

You are working inside a FreeCAD WAAM workbench repository.

Read `mission_control.yml` first and use it as the canonical project brief.

Build a **WAAM Mission Control** panel for this project with these requirements:

- Make it **workflow-first, not chat-first**.
- Build it as a **FreeCAD-integrated PySide/Qt panel or dialog**, not a web app.
- Reuse existing UI helpers and patterns already present in the repo, especially:
  - `Waam_tech/ui/qt_compat.py`
  - `Waam_tech/ui/mpl_compat.py`
  - `Waam_tech/ui/ui_workflow.py`
  - `Waam_tech/ui/ui_validator.py`
  - `Waam_tech/ui/torch_path_viewer.py`
  - `Waam_tech/ui/commands.py`
  - `Waam_tech/wb.py`

## Project context

This is a FreeCAD WAAM (Wire Arc Additive Manufacturing) module.

The canonical pipeline is:

FreeCAD model -> `features.json` -> `waam_operations.json` -> NC/G-code

The panel must respect these repo guardrails:

- Prefer WCS-aware logic.
- Preserve deposition start propagation.
- Keep behavior deterministic.
- Do not treat generated outputs as editable source by default.
- Keep changes backward compatible where possible.

## What to build

Create a Mission Control panel that gives a clear operational view of the WAAM workflow.

It should include these sections:

- Overview
- Pipeline
- Agents
- Artifacts
- Validation
- Logs

### Overview section

Show compact status cards for:

- Active document
- Current WCS
- Deposition start status
- Latest `features.json`
- Latest `waam_operations.json`
- Latest fused operations+mesh JSON
- Latest NC output
- Current risk level

### Pipeline section

Represent the workflow as explicit steps:

- Set WCS
- Extract Features
- Plan Operations
- Validate Layers
- Generate NC
- Review Output

Each step should show a status:

- `ready`
- `warning`
- `blocked`
- `error`

Status rules:

- `ready`: required input exists and the upstream step completed successfully
- `warning`: outputs exist but validation, review, or freshness is unclear
- `blocked`: required upstream artifact or document context is missing
- `error`: command failed, artifact is invalid, or validation did not pass

### Quick actions

Provide direct actions for:

- Open workflow dialog
- Run feature extraction
- Run deterministic planner
- Open validator
- Open torch path viewer
- Generate baseline NC
- Run waamgen validate

Where possible, wire these actions to existing project dialogs/commands instead of duplicating business logic.

### Agents section

Use the four-agent team described in `mission_control.yml`:

- Oliver Atom: lead
- Mark Lenders: builder
- Benji Price: critic
- Tom Baker: assistant

Show a simple per-agent view:

- Oliver: queue, priorities, acceptance criteria, final decision
- Mark: files in scope, implementation tasks, diffs
- Benji: findings, risks, regressions, missing tests
- Tom: context, commands, artifacts, summaries

### Artifacts section

Track and surface these paths:

- `Waam_tech/output/features/features.json`
- `Waam_tech/output/operations/waam_operations.json`
- `Waam_tech/output/operations_mesh/waam_operations_with_mesh.json`
- `Waam_tech/output/nc_files`
- `Waam_tech/output/torch_coverage`
- `Waam_tech/output/voxel_matrix`

Make generated artifacts clearly visible as outputs.

### Validation and warning emphasis

The panel should visibly highlight:

- WCS mismatches
- missing `deposition_start` propagation
- schema changes
- support trimming risk
- thin-wall collapse risk

Add a persistent safety banner near the top:

`WCS-aware, deterministic, do not edit generated outputs by default.`

## Implementation guidance

- Favor a clean industrial control-panel feel over a chat UI.
- Reuse existing command registration in `Waam_tech/ui/commands.py`.
- Add the new Mission Control entry to the WAAM workbench menu/toolbar.
- Keep the implementation focused and incremental.
- Prefer adding a new file such as `Waam_tech/ui/mission_control_panel.py` and only touch adjacent files needed for integration.
- Avoid large rewrites.

## Output expectations

Please:

1. Inspect the existing repo structure first.
2. Implement the Mission Control panel in code.
3. Integrate it into the existing WAAM workbench commands/menu.
4. Explain which files you changed.
5. Explain how the panel maps to `mission_control.yml`.
6. Note any assumptions or follow-up improvements.

Do not build a generic dashboard. Build a WAAM-specific Mission Control panel for this exact repository.
