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
- This is a FreeCAD WAAM (Wire Arc Additive Manufacturing) module that extracts features, plans operations, and generates G-code.
- The data flow is: FreeCAD model -> `features.json` -> `waam_operations.json` -> NC/G-code output.

## Key paths
- Feature extraction and metadata: `Waam_tech/part_features/feature_extract.py`
- Deterministic operation planning: `Waam_tech/operations/deterministic_planner.py`
- Baseline NC generator (bbox based): `Waam_tech/nc/nc_generator.py`
- WAAM pipeline (structured paths + trimming): `Waam_tech/waamgen/pipeline.py`
- WCS and deposition start marker: `Waam_tech/ui/wcs_marker.py`
- Main workflow orchestrator: `Waam_tech/ui/ui_workflow.py`
- Shared pipeline runner: `Waam_tech/ui/pipeline_runner.py`
- Experimental planners: `Waam_tech/experimental/`
- Validation logic: `Waam_tech/ui/ui_validator.py`
- STEP to WAAM DSL converter: `Waam_tech/llm_agent/step_to_waam_dsl.py`
- Analysis tools (voxel, torch coverage, mesh features): `Waam_tech/analysis/`
- Generated artifacts: `Waam_tech/output/` (treat as outputs unless explicitly asked to edit)

## Conventions
- Prefer WCS coordinates for geometry logic; when converting world -> WCS, use the helpers already in `feature_extract.py`.
- Keep behavior deterministic: stable ordering, explicit defaults, no randomization.
- Keep production logic in the main WAAM pipeline; keep part-specific iteration inside `Waam_tech/experimental/`.
- Be mindful of deposition start propagation: `wcs_marker` -> `feature_extract` -> `deterministic_planner` -> NC generation.
- Avoid non-ASCII unless a file already uses it.

## Common pitfalls
- Support trimming can remove interior paths on step features; check `Waam_tech/waamgen/pipeline.py` and `Waam_tech/nc/support_trim.py` when paths seem missing.
- The baseline NC generator uses feature bounding boxes, not full geometry; thin features smaller than a bead width may collapse.
- Experimental NC outputs should never overwrite `waam_baseline.nc` by default.

## Testing and validation
- If tests are present, run `pytest` from the repo root.
- When changing path planning, compare outputs in `Waam_tech/output/` or regenerate and diff.

## Output safety
- Do not edit generated NC or JSON outputs unless asked.
- Keep any new config keys backward compatible with existing JSON outputs.
