# Promoted Strategy: Solid Section Contour Then Hatch

## When To Use

- section topology: one outer contour and no inner voids
- expected geometry: bulk or simple closed section
- expected process goal: fill the section efficiently while keeping a predictable boundary

## Avoid When

- the same layer contains one or more true inner voids
- the section breaks into disconnected islands that need separate ordering logic

## Path Order

- first: outer contour
- then: additional contour pass only if wall thickness is too small for hatch
- finally: serpentine hatch across the full material span

## Torch Rules

- keep torch on for the contour
- keep torch on across each deposition hatch segment
- use torch-off travel only for normal repositioning between non-touching paths

## Deposition Rules

- contour rule: establish the boundary first
- infill rule: deposit continuously across the whole section width
- hole / void rule: not applicable in this strategy
- region transition rule: keep the fill direction alternating to reduce long return jumps

## Known Risks

- thermal: repeated short contour loops can overheat small sections
- short segments: very narrow sections may be better as contour-only
- continuity: poor hatch ordering increases empty travel

## Evidence

- golden fixture: `tests/fixtures/golden/waam_slice_plan.json`
  - `deposition_pattern`: `contour_hatch`
  - generated layers: 57 of 57 expected dense layers
  - generated structured paths: 3814
  - regression hook: `tests/test_golden_artifacts.py` freezes the plan-to-NC behavior
- archived run: `Waam_tech/experimental/runtime/archive/scc-908/20260409T105901Z`
  - observed notes: 80 outer contours, 0 inner contours, 320 fill/link paths, 0 hole crossings
  - reason it supports this strategy: no-void layers use boundary-first fill without hole-safe splitting
- promotion status: reusable strategy, but do not treat any single legacy archive manifest as validated unless its `review.status` is set to `validated`

## Related Code

- runtime orchestration: `Waam_tech/experimental/runtime/loader.py`
- knowledge wiring: `Waam_tech/experimental/layer_by_layer_scaffold.py`
- slice-plan source: `Waam_tech/slicing/slice_plan.py`
