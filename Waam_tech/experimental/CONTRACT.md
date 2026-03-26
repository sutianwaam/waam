# Experimental Iteration Contract

This folder is the only allowed editing area for AgentWAAM during WAAM iteration work.

## Rules

1. **Editing boundary**
   - AgentWAAM may only edit files inside `Waam_tech/experimental/` during experimental planner work.
   - The production WAAM pipeline remains the control path.
   - The live editable sandbox is `Waam_tech/experimental/runtime/active/`.

2. **One active part at a time**
   - Only one part may be marked active for experimental work.
   - Only one experimental planner should be considered active for that part.
   - When the active part changes, the previous runtime sandbox should be archived under `Waam_tech/experimental/runtime/archive/`.
   - The active part state should identify the part using `active_part_key`, `active_part_family`, and `goal`, not only the `.step` file name.

3. **Output convention**
   - `waam_baseline.nc` is the control output.
   - `waam_baseline_experimental.nc` is the candidate output.
   - `Run Pipeline + Experimental NC` should regenerate the control baseline first, then generate the candidate experimental NC.

4. **Traceability**
   - Every experiment should leave a small note including:
     - part name
     - goal
     - what changed
     - expected improvement
   - Runtime metadata should live in:
     - `Waam_tech/experimental/ACTIVE_PART.json`
     - `Waam_tech/experimental/runtime/active/manifest.json`
     - `Waam_tech/experimental/runtime/active/notes.md`
     - `Waam_tech/experimental/runtime/active/prompt_context.json`

5. **Allowed runtime files**
   - AgentWAAM may edit only:
     - `Waam_tech/experimental/runtime/active/planner.py`
     - `Waam_tech/experimental/runtime/active/helpers.py`
     - `Waam_tech/experimental/runtime/active/notes.md`
     - `Waam_tech/experimental/runtime/active/manifest.json`
     - `Waam_tech/experimental/runtime/active/prompt_context.json`
   - Older named planners live under `Waam_tech/experimental/legacy/` and should be treated as archived reference implementations, not the main iteration path.

6. **Selection safety**
   - If the experimental planner cannot be selected safely, the process should fail loudly instead of silently falling back to the wrong planner.

## Practical intent
This contract exists to keep iteration disciplined:
- stable control output
- isolated experimental logic
- one active experiment at a time
- visible hypothesis for every change
- archived memory of previous part-specific experiments
