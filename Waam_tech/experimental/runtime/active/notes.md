# Active Experiment Notes: Part__Feature

## Goal
Preserve 4 holes and add material deposition in the base region in waam_baseline_experimental.nc

## Contract
- Edit only `Waam_tech/experimental/`.
- Keep `waam_baseline.nc` as the control output.
- Write candidate changes into `waam_baseline_experimental.nc`.

## Current Hypothesis
- Start from the baseline NC copy and add only part-specific experimental logic.
- Keep changes local to `runtime/active/planner.py` and `runtime/active/helpers.py`.
## Experimental fused region-R0 infill override (2026-04-01T09:52:27Z)
- fused override path: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_operations_with_mesh_experimental.json
- layers changed: 0
- strategy: prepend one extra boundary-aligned experimental infill line to region R0 in the fused structured path data before NC generation
- scope: experimental only; production fused data untouched

## Experimental fused infill override export (2026-04-01T09:52:27Z)
- part: part-feature
- label: Part__Feature
- goal: Preserve 4 holes and add material deposition in the base region in waam_baseline_experimental.nc
- strategy: modify the fused structured path data for region R0 inside experimental only, then regenerate NC
- intent: eliminate the persistent start-edge void without changing production generator code

## Experimental small-part override export (2026-04-01T10:33:28Z)
- fused override path: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_operations_with_mesh_experimental.json
- override file: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/small_part_config_override.json
- override applied: False
- strategy: inject small-part process/path parameters into an experimental fused clone before NC generation
- scope: experimental only; production config untouched

## Experimental small-part runtime export (2026-04-01T10:33:28Z)
- part: part-feature
- label: Part__Feature
- goal: Preserve 4 holes and add material deposition in the base region in waam_baseline_experimental.nc
- strategy: use experimental small-part config override during experimental NC generation
- intent: improve planning resolution for tiny geometries without touching production config

## Experimental TOPO_INFILL fragment reorder export (2026-04-03T18:53:15Z)
- fused override path: /Users/sustian/Library/Preferences/FreeCAD/Mod/WAAM/Waam_tech/output/operations_mesh/waam_operations_with_mesh_experimental.json
- small-part override applied: False
- changed layers: 182
- strategy: reorder fragmented TOPO_INFILL path_ids spatially inside experimental fused toolpath/timeline before NC generation
- scope: experimental only; production fused data untouched

## Experimental fragmented infill reorder runtime export (2026-04-03T18:53:15Z)
- part: part-feature
- label: Part__Feature
- goal: Preserve 4 holes and add material deposition in the base region in waam_baseline_experimental.nc
- strategy: reorder fragmented early TOPO_INFILL path lists coherently before experimental NC generation
- intent: reduce the strange criss-cross start without touching production code

## Experimental constant cross-section repeated-layer export (2026-04-03T18:59:39Z)
- template layer index: 0
- template contours: 9
- template infill paths: 238
- strategy: repeat one constant cross-section layer pattern upward while preserving the 2-hole geometry encoded in the template paths

## Experimental constant-section runtime export (2026-04-03T18:59:39Z)
- part: part-feature
- label: Part__Feature
- goal: Preserve 4 holes and add material deposition in the base region in waam_baseline_experimental.nc
- strategy: reuse a constant cross-section template layer and repeat it upward with 2-hole preservation
- intent: avoid fragmented topology-sensitive starts for vertically stable two-hole parts

## Experimental constant cross-section repeated-layer export (2026-04-03T19:05:44Z)
- template layer index: 0
- template contours: 9
- template infill paths: 238
- strategy: repeat one constant cross-section layer pattern upward while preserving the 2-hole geometry encoded in the template paths

## Experimental constant-section runtime export (2026-04-03T19:05:44Z)
- part: part-feature
- label: Part__Feature
- goal: Preserve 4 holes and add material deposition in the base region in waam_baseline_experimental.nc
- strategy: reuse a constant cross-section template layer and repeat it upward with 2-hole preservation
- intent: avoid fragmented topology-sensitive starts for vertically stable two-hole parts

## Experimental constant cross-section repeated-layer export (2026-04-03T19:11:07Z)
- template layer index: 0
- template contours: 9
- template infill paths: 0
- strategy: repeat one constant cross-section layer pattern upward while reordering template paths locally to reduce long travel diagonals

## Experimental constant-section runtime export (2026-04-03T19:11:07Z)
- part: part-feature
- label: Part__Feature
- goal: Preserve 4 holes and add material deposition in the base region in waam_baseline_experimental.nc
- strategy: reuse a constant cross-section template layer and repeat it upward with 2-hole preservation
- intent: avoid fragmented topology-sensitive starts for vertically stable two-hole parts

