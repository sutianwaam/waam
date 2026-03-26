# Pieza UPNA

## Part identity
- part key: `pieza-upna`
- geometry family: thin-wall frame / bracket-like WAAM part
- status: active experimental case study

## Geometry summary
- large central elongated opening
- smaller angled / triangular internal openings
- multiple narrow webs and transitions between outer frame and inner cutout boundaries
- intended objective: improve `waam_baseline.nc` so the path is more WAAM-appropriate for thin-wall geometry and internal openings

## Build orientation assumption
- current assumption: build in the orientation used for the generated baseline preview
- status: needs verification against intended build Z and support logic

## Expected topology zones
- Zone 1:
  base establishment of the outer frame and lower wall network, preserving all intended openings
- Zone 2:
  stable thin-wall growth through the frame and internal opening boundaries
- Zone 3:
  final upper closure / continuation of the same wall-driven topology with minimal discontinuity

## Baseline NC observations
- current baseline appears too naive and too geometry-only
- path seems to repeat outer/inner boundaries without a strong thin-wall strategy
- internal opening transitions and narrow webs look likely to suffer from poor local path behavior
- result resembles generic offset contouring more than process-aware WAAM planning

## Failure modes observed
- thin-wall regions are not clearly being treated as single-track / dual-track wall features
- likely too many discontinuities or awkward transitions in the internal frame structure
- junctions and narrow corner transitions likely use raw offset logic, which risks voids or unstable bead buildup
- path strategy does not yet appear feature-class aware

## Experimental planner path
- use the experimental architecture under `Waam_tech/experimental/`
- avoid modifying the main baseline generator directly
- this part is a candidate for a thin-wall / frame-specific experimental planner

## Iterations tried
- Attempt 1:
  standard pipeline / baseline NC reviewed visually from screenshot
- Attempt 2:
  not yet started
- Attempt 3:
  not yet started

## Current best result
- current best result is still the baseline produced by the normal pipeline
- it is useful for diagnosis, but not yet convincing as an optimized WAAM path for this geometry

## Reusable lessons
- frame-like parts with internal openings should not automatically be treated as bulk-fill regions
- thin-wall geometry should trigger wall-aware planning rather than generic contour repetition
- junction / corner correction is likely important for this geometry family

## Promotion candidates
- planner-pattern candidate:
  thin-wall frame parts with internal openings should use wall-aware, continuity-first planning
- failure-mode candidate:
  generic contour repetition is not enough for frame / web parts
- production promotion:
  nothing should be promoted into the main pipeline yet
