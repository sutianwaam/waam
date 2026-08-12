# Promoted Strategy: Voided Section Hole Safe Fill

## When To Use

- section topology: one outer contour with one or more inner voids
- expected geometry: plates, brackets, or bulk sections with holes or pockets crossing the current layer
- expected process goal: preserve void boundaries while still filling all valid material windows

## Avoid When

- there are no voids in the current section
- the section is contour-only because the remaining span is too narrow for fill

## Path Order

- first: outer contour
- then: inner void contours
- finally: segmented fill over the valid material windows only

## Torch Rules

- torch on for outer and inner contours
- torch on only inside material windows during fill
- torch off across void crossings using explicit link or travel segments

## Deposition Rules

- contour rule: lock the outer and void boundaries before fill
- infill rule: split each fill stripe at every hole window
- hole / void rule: never deposit across a hole span
- region transition rule: prefer serpentine progression with explicit torch-off links across cut windows

## Known Risks

- thermal: many short deposition windows can create too many starts and stops
- short segments: excessive fragmentation may make the process noisy or inefficient
- continuity: continuity logic must not reconnect deposition across a void

## Evidence

- archived run: `Waam_tech/experimental/runtime/archive/schenkel/20260408T135219Z`
  - observed notes: 155 outer contours, 109 inner contours, 193 fill/link paths, 45 torch-off hole crossings
  - reason it supports this strategy: inner contours were preserved before fill and void crossings were explicit links
- archived run: `Waam_tech/experimental/runtime/archive/crankcase/20260408T133612Z`
  - observed notes: 90 outer contours, 48 inner contours, 744 fill/link paths, 144 torch-off hole crossings
  - reason it supports this strategy: segmented fill avoided depositing through detected void windows
- archived run: `Waam_tech/experimental/runtime/archive/pieza2/20260408T134126Z`
  - observed notes: 7 outer contours, 21 inner contours, 280 fill/link paths, 112 torch-off hole crossings
  - reason it supports this strategy: repeated contour-then-fill trials showed the need to preserve inner loops and links
- promotion status: reusable strategy, but require `review.status: validated` plus archive/plan/NC evidence before seeding production behavior

## Related Code

- runtime orchestration: `Waam_tech/experimental/runtime/loader.py`
- knowledge wiring: `Waam_tech/experimental/layer_by_layer_scaffold.py`
- slice-plan source: `Waam_tech/slicing/slice_plan.py`
