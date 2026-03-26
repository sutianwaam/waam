# StudyCadCam 78

## Part identity
- part key: `studycadcam-78`
- geometry family: multi-hole WAAM part under study
- status: active diagnostic case study

## Geometry summary
- WAAM part under study from `StudyCadCam 78.step`
- visual review indicates a part with 4 relevant holes total
- two holes are being considered in the current baseline path, while two holes on the bottom face are not being preserved correctly

## Build orientation assumption
- current orientation is the one used in the present FreeCAD WAAM baseline preview
- status: needs explicit verification once the STEP is studied directly

## Expected topology zones
- Zone 1:
  base region where bottom-face holes must not be collapsed into solid material
- Zone 2:
  middle transition region where the relation between hole sets and walls must remain explicit
- Zone 3:
  upper region where the back / upper hole pair remains preserved

## Baseline NC observations
- current `waam_baseline.nc` appears to preserve only 2 of the 4 holes
- the 2 holes on the bottom face are not being represented correctly in the experimental baseline view

## Failure modes observed
- partial hole recognition
- feature handling likely biased toward one dominant section family
- geometry semantics are being lost for holes belonging to another face / region

## Experimental planner path
- a dedicated experimental planner should be implemented under `Waam_tech/experimental/` for this part
- goal: explicit 4-hole-aware geometry handling before any further NC iteration

## Iterations tried
- Attempt 1:
  standard pipeline / baseline NC visual review identified missing preservation of the bottom-face hole pair
- Attempt 2:
  not yet started
- Attempt 3:
  not yet started

## Current best result
- current best result is still the standard baseline, but it is incomplete because only 2 of 4 holes are respected

## Reusable lessons
- hole preservation must not depend only on the most obvious slice family
- multi-face hole sets need explicit feature inventory and classification

## Promotion candidates
- failure-mode candidate:
  planners may preserve only one visible hole family and erase others
- production promotion:
  nothing should be promoted into the main pipeline yet
