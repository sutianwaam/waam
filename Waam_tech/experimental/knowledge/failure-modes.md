# Failure Modes

This file stores recurring baseline and planner failures that should be checked before designing a new WAAM strategy.

## How to use this file
- read this before trusting a baseline NC for a new part
- use it as a checklist of common planning mistakes
- when a failure repeats across parts, record it here in generalized form
- each failure mode should describe:
  - symptoms
  - observed pattern
  - implication
  - likely planner response

## Failure mode: early fill seals intended holes or windows
### Symptoms
- baseline NC deposits material in regions that should remain open
- inner voids appear too late in the slice evolution

### Observed pattern
- same-layer geometry interpretation is too aggressive
- planner treats transitional topology as solid area fill

### Implication
- suppressing some fill is not enough by itself
- true fix requires explicit inner-loop or persistent-void handling

### Likely planner response
- use conservative early-layer logic
- introduce protected-void reasoning before interior fill

## Failure mode: build direction inversion
### Symptoms
- early layers resemble top-of-part geometry
- toolpath appears to build from top down instead of substrate up

### Observed pattern
- layer order, build orientation, or interpretation of the slice stack may be wrong

### Implication
- trust in the NC becomes questionable even if the local paths look plausible

### Likely planner response
- validate that the first deposited layer corresponds to the lowest supported slice
- verify build Z assumptions before path refinement

## Failure mode: bounding-box simplification collapses geometry intent
### Symptoms
- broad fill ignores real topology
- thin or internal features are approximated away

### Observed pattern
- bbox-style simplification is acting as a geometry surrogate where topology-sensitive reasoning is required

### Implication
- bbox-based baseline generation is useful as a prototype, but not sufficient for topology-sensitive parts

### Likely planner response
- replace bbox logic with contour-, region-, or topology-aware decomposition

## Failure mode: topology transition handled too late
### Symptoms
- disconnected early regions merge into a solid mass before holes are preserved
- openings are only recognized once the slicer emits explicit inner loops much later

### Observed pattern
- planner relies too heavily on per-layer raw loop count
- continuity across neighboring slices is ignored

### Implication
- planner needs continuity-aware topology inference, not just per-layer raw loop count

### Likely planner response
- infer topology transitions across height, not only from a single layer snapshot

## Failure mode: only one hole family is preserved
### Symptoms
- some holes are respected, but another face- or region-dependent hole set is erased
- path logic appears biased toward the most obvious or dominant section family

### Observed pattern
- feature inventory is incomplete
- planner locks onto one geometric interpretation and ignores another valid hole family

### Implication
- multi-face or multi-region hole sets require explicit classification before path generation

### Likely planner response
- inventory all relevant hole families and preserve them intentionally rather than incidentally

## Failure mode: frame / web geometry treated like bulk fill
### Symptoms
- thin-wall frame parts receive generic contour repetition or bulk-style fill behavior
- webs, narrow transitions, and internal openings are poorly respected

### Observed pattern
- local geometry class is not recognized before path-family selection

### Implication
- frame-like parts need wall-aware planning, not default bulk logic

### Likely planner response
- classify thin-wall and frame geometry first, then choose continuity-first wall strategies
