# WAAM Toolpath Key Points

## Purpose
This note extracts and generalizes the most useful ideas from *Tool Path Planning for Wire-Arc Additive Manufacturing Processes* by Qui Lam Nguyen for one practical goal:

**generate better tool paths so a WAAM `.nc` file deposits stable beads, avoids defects, preserves geometry, and is easier to execute in a FreeCAD WAAM workflow.**

This is not a chapter-by-chapter copy. It is a module-oriented synthesis focused on what matters most when converting a general CAD part into a usable WAAM deposition path.

## Best used for
- planner design
- NC strategy review
- deciding what a WAAM path generator should optimize first
- translating process-aware WAAM knowledge into FreeCAD module behavior

## Core thesis
> Path planning must be process-aware, not only geometry-aware.

A useful WAAM CAM module is not just a contour exporter. It must account for bead geometry, continuity, topology preservation, local defect risk, and future machine execution.

## Executive summary
- the path controls part quality, not just geometry coverage
- bead-aware planning is mandatory
- continuity matters because start/stop events create defects
- boundary and interior spacing should not be treated identically
- feature type should drive path-family choice
- corners, junctions, and branch intersections need explicit correction
- travel speed and segment metadata are part of geometry control
- adaptive slicing and multi-axis planning matter for harder parts
- distortion-aware thinking should exist before export, not only after printing

## 1. The path is not just geometry; it controls part quality
For WAAM, the toolpath directly affects:
- weld bead shape
- layer flatness
- surface waviness
- void formation at corners and junctions
- number of arc start/stop events
- heat accumulation
- distortion and shrinkage
- final dimensional accuracy
- deposition efficiency

**Practical rule:** the best `.nc` file is not merely contour-following; it is the one that builds the part with the fewest defects and the most stable deposition conditions.

## 2. The first requirement is bead-aware planning
A WAAM path planner should never behave like a generic FDM path generator.

### Required planner inputs
- nominal bead width `w`
- nominal bead height `h`
- recommended overlap / step-over
- allowed minimum and maximum layer thickness

**Practical rule:** without bead-aware spacing, a path can be geometrically correct in CAD but wrong for real deposition.

## 3. Continuous motion is one of the most important goals
Continuous tool motion reduces defects.

### Why this matters
- every extra start/stop can produce uneven bead shape
- stopping at corners increases local heat input
- restarting the arc raises risk of underfill, overfill, and poor local geometry
- rapid jumps reduce process efficiency

### Preferred decision order
1. one continuous path, if possible
2. otherwise the minimum number of separate weld passes
3. otherwise a segmentation that hides start/stop points in low-critical areas

**Practical rule:** when two valid path options exist, prefer the one with fewer ignitions, fewer discontinuities, smoother speed profile, and fewer sharp reversals.

## 4. Boundary and interior paths should not use the same overlap blindly
Edge beads behave differently from interior beads.

### Required distinction
- interior beads
- boundary / contour beads

### Module implication
Support at least:
- `interior_stepover`
- `boundary_stepover`

**Practical rule:** do not assume one global stepover works for every pass.

## 5. Path pattern choice should follow part function
The best path is not universal; it depends on the local feature type.

### Suggested mapping
- outer surfaces → contour-following paths
- bulky interiors → fill strategies with controlled overlap
- thin walls / rib-web structures → continuous contour-based paths
- closed hollow or curved thin-walled parts → continuous paths whenever possible

**Practical rule:** choose path family from geometry class, not from one global default.

## 6. Junctions, corners, and branch intersections need special treatment
Raw offsets are not trustworthy at intersections.

### Detect and classify
- sharp internal corners
- branch junctions
- crossing walls
- multi-rib intersections
- tight turns

### Possible local corrections
- vertex shift
- local bead widening
- local dwell avoidance
- local extra material path
- corner blending

**Minimum viable implementation:** corner detection, junction detection, local path correction, and blending instead of stop-and-turn motion.

## 7. Constant travel speed is highly valuable
Stopping exactly at turning points can worsen deposition.

### NC implication
- maintain steady feed / travel speed where possible
- smooth curvature transitions
- avoid unnecessary pauses near corners

### Implementation idea
Use a path post-processor that:
- rounds very sharp internal turns when allowed
- inserts blend radii or smoothing segments
- limits sudden feed changes
- avoids exact stop behavior except where absolutely necessary

## 8. Multi-axis planning is essential for overhangs and inclined features
Planar WAAM is limited for large overhangs and inclined features.

### Stronger future capability
- local build direction per feature
- surface normal extraction
- tool orientation output
- positioner axis output when available

**Practical rule:** if a feature cannot be deposited well in global `+Z`, consider decomposition or reorientation.

## 9. Adaptive slicing is better than one fixed layer height for all geometries
A single constant layer height is often too crude for:
- curved walls
- non-planar geometries
- hollow curved profiles
- variable local curvature

### Recommended modes
- constant layer height
- adaptive layer height based on geometry
- constrained adaptive slicing based on feasible bead height range

**Practical rule:** layer strategy should respect what bead heights are actually depositable.

## 10. Travel speed can be used as a geometry control variable
A path is not only coordinates. It should carry process intent.

### Useful per-segment metadata
- feed rate per segment
- welding enable / disable
- local layer index
- local tool orientation
- local positioner angles

**Practical rule:** every path segment can carry both geometry data and process data.

## 11. Shrinkage and distortion must be considered before export
Shrinkage and distortion are unavoidable in WAAM.

### Future-ready capabilities
- compensation maps
- scaling / bending correction fields
- experimental correction profiles per material and geometry family

**Practical rule:** even if full compensation is not implemented, design the workflow so it can accept compensation later.

## 12. A good WAAM `.nc` file should contain more than XYZ points
An industrial WAAM program is trajectory plus process state.

### Recommended fields
- `layer_index`
- `x, y, z`
- `tool_orientation` or surface normal
- `feed_rate`
- `weld_enable`
- `segment_type`
- optional `positioner_a`, `positioner_b`
- optional `bead_width_target`
- optional `bead_height_target`

## 13. Best-practice strategy for a general part
### Step 1 — Analyze geometry
Detect:
- thin walls
- bulky regions
- sharp internal corners
- junctions / branches
- holes / islands
- overhangs
- highly curved regions

### Step 2 — Choose local path family
- thin walls: contour / centerline-based continuous path
- solid bulk: contour + controlled interior fill
- rib-web regions: continuous connected contours
- overhangs: multi-axis or feature decomposition
- curved thin walls: adaptive slicing + continuous deposition

### Step 3 — Apply bead-aware spacing
Use bead model to determine:
- layer height
- stepover
- boundary correction
- fill density

### Step 4 — Reduce discontinuities
Optimize for:
- minimum number of weld starts
- minimum rapid moves
- smooth ordering of regions

### Step 5 — Correct critical local features
Apply local rules for:
- corners
- junctions
- narrow passages
- ends of closed loops

### Step 6 — Attach process parameters
Assign per segment:
- feed rate
- welding on/off
- orientation
- optional thermal or compensation metadata

### Step 7 — Export to NC
Post-process into machine syntax while preserving:
- motion continuity
- blending where needed
- segment metadata
- machine-specific axes and commands

## 14. Priority order for “best path” decisions
1. deposition stability
2. continuous motion / fewer starts and stops
3. correct bead overlap
4. junction / corner defect prevention
5. boundary accuracy
6. support for overhangs through reorientation
7. adaptive slicing for curved features
8. distortion compensation
9. process time optimization

**Practical rule:** a fast path is not a good path if it creates voids, uneven layers, or major distortion.

## 15. Recommended minimum feature set for the FreeCAD WAAM module
### High-value first version
- bead-width and layer-height driven slicing / path spacing
- separate boundary and interior overlap logic
- continuous path ordering
- junction / corner correction
- welding on/off tags in export
- segment feed-rate control
- optional orientation output for future multi-axis support

### Later advanced version
- Eulerian path generation for rib-web style regions
- adaptive slicing for curved parts
- non-planar / multi-axis deposition planning
- empirical distortion compensation

## Final takeaway
For WAAM, the best `.nc` file does not come from simply converting contours into lines.

It comes from a planner that understands:
- bead geometry
- overlap behavior
- continuity of motion
- local defect formation at corners and junctions
- feature-dependent strategy selection
- multi-axis needs for overhangs
- compensation for real welding behavior

## Source
Primary source used for this summary:
- Qui Lam Nguyen, *Tool Path Planning for Wire-Arc Additive Manufacturing Processes*, Brandenburg University of Technology Cottbus–Senftenberg, 2022. DOI: 10.26127/BTUOpen-5982.
