# AGENTS

This repo uses the canonical OpenClaw agent structure from:

- `/Users/sustian/.openclaw/workspace/agents/`

Treat that folder as the single source of truth for team structure, roles, routing, and operating model.

## Canonical agent docs
Refer to these files in the OpenClaw workspace:
- `/Users/sustian/.openclaw/workspace/agents/README.md`
- `/Users/sustian/.openclaw/workspace/agents/TEAM.md`
- `/Users/sustian/.openclaw/workspace/agents/WORKFLOW.md`
- `/Users/sustian/.openclaw/workspace/agents/ROUTING.md`
- `/Users/sustian/.openclaw/workspace/agents/DISCORD.md`
- `/Users/sustian/.openclaw/workspace/agents/roster.yaml`

## Repo overview
- This is a FreeCAD WAAM (Wire Arc Additive Manufacturing) module that slices a part in WCS-local XY layers, creates deposition paths, and generates G-code.
- The primary data flow is: FreeCAD model -> `waam_slice_plan.json` -> NC/G-code output.
- Auxiliary artifacts may also be written during the lite workflow: `features.json` for metadata and `part.llm_waam` for compact LLM-oriented context.

## Key paths
- Slice planner: `Waam_tech/slicing/slice_plan.py`
- STEP to compact WAAM DSL converter: `Waam_tech/slicing/step_to_waam_dsl.py`
- WAAM NC pipeline (structured paths + trimming): `Waam_tech/waamgen/pipeline.py`
- Standard process defaults: `Waam_tech/config/standard_waam.json`
- WCS and deposition start marker: `Waam_tech/ui/wcs_marker.py`
- Main slice-planner and NC dialogs: `Waam_tech/ui/ui_workflow.py`
- Shared pipeline runner: `Waam_tech/ui/pipeline_runner.py`
- Experimental planners and per-project iteration area: `Waam_tech/experimental/`
- Active experimental runtime sandbox for iterative planner changes: `Waam_tech/experimental/runtime/active/`
- Generated artifacts: `Waam_tech/output/` (treat as outputs unless explicitly asked to edit)

## Conventions
- Prefer WCS coordinates for geometry logic; transform geometry into the active WCS before slicing.
- Keep behavior deterministic: stable ordering, explicit defaults, no randomization.
- Keep production logic in the main WAAM pipeline; keep part-specific or project-specific iteration inside `Waam_tech/experimental/`.
- When adapting planners for a specific project, prefer editing the experimental runtime sandbox under `Waam_tech/experimental/runtime/active/` instead of changing the baseline lite pipeline first.
- Be mindful of deposition start propagation: `wcs_marker` -> `slice_plan` -> NC generation.
- Avoid non-ASCII unless a file already uses it.

## Common pitfalls
- Support trimming can remove interior paths on step features; check `Waam_tech/waamgen/pipeline.py` and `Waam_tech/nc/support_trim.py` when paths seem missing.
- If a part produces far fewer layers than its WCS bbox height suggests, section extraction or WCS orientation is likely wrong.
- Experimental NC outputs should never overwrite `waam_baseline.nc` by default.

## Testing and validation
- If tests are present, run `pytest` from the repo root.
- When changing path planning, compare outputs in `Waam_tech/output/` or regenerate and diff.

## Output safety
- Do not edit generated NC or JSON outputs under `Waam_tech/output/` unless asked.
- The exception is intentional iteration work inside `Waam_tech/experimental/runtime/active/`, which is expected to be updated when evolving the experimental planner.
- Keep any new config keys backward compatible with existing JSON outputs.
