# Planner Patterns

This file stores reusable planning strategies that should transfer across multiple WAAM parts.

## How to use this file
- read this before designing a new experimental planner
- prefer patterns here over ad hoc part-specific logic
- when a part note proves a strategy repeatedly, promote the distilled rule here
- each pattern should describe:
  - when to use it
  - why it helps
  - what the planner should do

## Pattern: three-zone reasoning for topology-changing parts
### Use when
- the part evolves through clearly different slice regimes over height
- lower, middle, and upper regions need different logic

### Why it helps
- avoids treating the entire part as one uniform topology problem
- makes transitions explicit instead of accidental

### Planner rule
- Zone 1: supportive base establishment
- Zone 2: topology transition
- Zone 3: stable mature section

### Typical implementation hint
- infer zones from slice topology changes, hole count changes, or wall-merge / wall-split events

## Pattern: contour-dominant base strategy
### Use when
- lower layers risk sealing holes, windows, or protected voids
- early slice interpretation is ambiguous

### Why it helps
- avoids aggressive early fill that destroys future geometry intent

### Planner rule
- favor outer contours and limited stabilizing contours
- avoid blanket fill in early ambiguous layers
- do not assume every enclosed area should be filled

### Typical implementation hint
- use conservative early-layer deposition until the topology becomes stable enough to justify interior fill

## Pattern: persistent hole / void preservation
### Use when
- important openings emerge gradually with height
- same-layer slicing reveals holes too late

### Why it helps
- protects geometry intent before the raw slice data becomes explicit

### Planner rule
- infer important holes from neighboring slices or stable future topology
- treat them as protected void masks before same-layer slicing becomes explicit

### Typical implementation hint
- propagate protected voids downward or across neighboring layers when topology evidence is strong

## Pattern: partitioned fill instead of monolithic fill
### Use when
- a part contains internal holes, windows, or separated material bands
- a single area-fill pass would bridge across forbidden voids

### Why it helps
- preserves topology boundaries and prevents one giant fill strategy from erasing local structure

### Planner rule
- outer contour first
- inner contours second
- fill only material subregions between boundaries
- avoid one giant spiral or area fill across the whole section

### Typical implementation hint
- decompose each slice into topology-aware material regions before generating fill

## Pattern: wall-aware planning for thin-wall frame parts
### Use when
- the part is frame-like, web-like, or thin-wall dominant
- internal openings are part of the main geometry, not just subtractive details

### Why it helps
- generic contour repetition or bulk-fill logic is often inappropriate for frame geometry

### Planner rule
- treat walls as primary deposition entities
- favor continuity-first planning through the frame structure
- avoid classifying the part as a bulk region unless the local geometry truly supports it

### Typical implementation hint
- classify thin walls, ribs, and internal frame segments before assigning contour/fill behavior

## Pattern: experimental planner isolation
### Use when
- iterating on a specific part or geometry family
- testing logic that is not yet mature enough for the production baseline generator

### Why it helps
- keeps the production pipeline stable while still allowing fast experimentation

### Planner rule
- keep production baseline generator unchanged
- place part-specific logic under `Waam_tech/experimental/`
- emit separate experimental NC outputs instead of overwriting `waam_baseline.nc`

### Typical implementation hint
- promote only distilled, repeated wins into the main pipeline
