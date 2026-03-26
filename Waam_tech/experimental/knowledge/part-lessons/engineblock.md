# EngineBlock

## Part identity
- part key: `engineblock`
- geometry family: topology-changing WAAM benchmark part
- status: active experimental case study

## Geometry summary
- changing topology through build height
- broad base / wall regions, transition zone, and upper region with important openings
- intended goal: improve `waam_baseline.nc` quality for this part without destabilizing the main WAAM pipeline

## Key lesson
- the initial baseline and first experimental remaps were not enough to preserve the two important openings in the lower region

## Main observed issues
- build-direction suspicion and NC trustworthiness concerns
- early fill deposited material where two holes / openings should remain
- making the experimental file merely different was not sufficient; geometry intent still was not preserved

## Useful conceptual model
Use a three-zone interpretation:
- Zone 1: base establishment with hole protection
- Zone 2: topology transition with explicit outer + inner logic
- Zone 3: stable outer / inner contour logic with partitioned fill

## Architectural outcome
Instead of modifying the main WAAM generator directly:
- keep production pipeline stable
- isolate part-specific work in `Waam_tech/experimental/`
- add a general experimental NC button / path
- use repo-side knowledge + OpenClaw memory together for future reuse

## Reusable lessons
- future parts should get a part note here before heavy iteration starts
- if a strategy repeatedly works, promote the distilled rule into `planner-patterns.md`
- if a failure repeats, record it in `failure-modes.md`
