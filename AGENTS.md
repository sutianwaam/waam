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

## Pipeline checkpoints

The slice-first pipeline is deterministic (same part + same WCS + same
config -> same trajectory), which is what makes regression validation
against stored references possible before physical deposition. That
determinism is enforced at three explicit, machine-checkable checkpoints:

```text
FreeCAD model
   |
   |  (slice_plan.write_slice_plan_for_document)
   v
[checkpoint A] waam_slice_plan.json + <plan>.manifest.json sidecar
   |            - metadata.stage_manifest: model hash, WCS name/source/warnings,
   |              config hash, layer/path counts, deposition start + source
   |
   |  (waamgen.plan_schema.validate_plan_schema, called by load_light_job)
   v
[checkpoint B] schema validation
   |            - required keys present, WCS non-empty, layers non-empty,
   |              monotonic Z (warning if not) -- fails fast, names the
   |              stage that produced bad data
   |
   |  (waamgen.pipeline.generate_nc)
   v
NC output (waam_baseline.nc / waam_baseline_experimental.nc)
   |            - header comments carry source plan filename + hash,
   |              config hash, and generator version for traceability
   |
   |  (tools/waam_regress.py, tools/plan_regress.py, pytest tests/)
   v
[checkpoint C] validate / regress
                - byte identity, move-by-move equivalence, torch-event
                  sequencing, semantic plan diff -- against golden fixtures
                  or any two artifacts
```

## Testing and validation
- Golden-artifact and unit tests live in `tests/` and run WITHOUT FreeCAD:
  `python -m pytest -q` from the repo root (install `requirements-dev.txt` first).
- The pytest suite includes import-shimmed pure slicing tests for helpers in
  `Waam_tech/slicing/`, but FreeCAD kernel behavior (real STEP sectioning,
  Path Job objects, and GUI document integration) remains an integration-test
  coverage gap that must be checked inside FreeCAD.
- `tools/waam_regress.py candidate.nc reference.nc` compares two NC files
  (byte identity, then move/torch/geometry equivalence within tolerance).
- `tools/plan_regress.py candidate.json reference.json` semantically diffs
  two `waam_slice_plan.json` files (layers, paths, geometry, process
  parameters), ignoring volatile metadata (timestamps, absolute paths).
- `python -m Waam_tech.waamgen.pipeline validate-plan --plan <path>` runs
  checkpoint B standalone.
- When changing path planning, compare outputs in `Waam_tech/output/` or regenerate and diff.

## Agent roles (orchestration layer)

This repo uses the canonical OpenClaw agent structure (see top of this
file) for team structure and routing. Layered on top of that, these are the
WAAM-specific roles and the concrete commands each one runs -- these checks
are meant to be executed, not just described:

- **TRIAGE**: classify an incoming issue/request and route it to a
  checkpoint.
  - Geometry problem (missing paths, wrong layer count) -> checkpoint A/B:
    inspect `metadata.slice_diagnostics` and `metadata.stage_manifest` in
    the plan JSON; run
    `python -m Waam_tech.waamgen.pipeline validate-plan --plan <plan.json>`.
  - Config/process-parameter problem -> checkpoint B:
    `python -m Waam_tech.waamgen.pipeline validate --gcode <nc> --ref <plan.json>`.
  - Planner regression (output changed vs. before) -> checkpoint C:
    `python tools/plan_regress.py <old_plan.json> <new_plan.json>` and
    `python tools/waam_regress.py <old.nc> <new.nc>`.

- **GEOMETRIC ANALYSIS**: inspect slice plans and NC stats for coverage
  problems (layer counts vs. bbox height, missing interior paths after
  support trim).
  - `python tools/plan_regress.py <plan_a.json> <plan_b.json>` for a
    semantic diff (layer/path counts, per-layer Z, path geometry).
  - `python tools/waam_regress.py <nc_a> <nc_b>` for move-by-move,
    torch-sequencing, and bounding-box comparison.

- **CRITICAL REVIEW**: any change to process parameters or planner logic
  must pass regression comparison against stored references before
  acceptance.
  - `python -m pytest -q` (golden artifacts, repeatability, validator
    pass-through, unit tests -- all FreeCAD-free).
  - `python tools/waam_regress.py <candidate.nc> tests/fixtures/golden/waam_baseline.nc`
  - `python tools/plan_regress.py <candidate.json> tests/fixtures/golden/waam_slice_plan.json`

## Output safety
- Do not edit generated NC or JSON outputs under `Waam_tech/output/` unless asked.
- The exception is intentional iteration work inside `Waam_tech/experimental/runtime/active/`, which is expected to be updated when evolving the experimental planner.
- Keep any new config keys backward compatible with existing JSON outputs.
- `Waam_tech/output/**` is gitignored: it holds locally-generated artifacts,
  not committed references. The committed regression references live in
  `tests/fixtures/golden/` and are frozen copies -- update them deliberately
  (see IMPROVEMENTS.md) rather than assuming `Waam_tech/output/` is a
  permanent source of truth.
