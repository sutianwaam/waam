# WAAM Experimental Knowledge

This folder stores reusable knowledge learned while iterating part-specific experimental planners.

## Purpose
- keep part-specific lessons out of the main WAAM pipeline
- retain reusable WAAM knowledge across future parts
- make experimental improvements traceable before promoting anything into production logic
- make AI-assisted review easier by separating:
  - stable reference knowledge
  - reusable planning rules
  - recurring failure patterns
  - part-specific case studies

## Recommended AI read order
When an AI model studies this folder for a new part or planner task, read in this order:

1. `README.md`
   - understand folder purpose and navigation
2. `planner-patterns.md`
   - learn reusable planning strategies
3. `failure-modes.md`
   - learn common mistakes and what to avoid
4. `waam-toolpath-key-points.md`
   - load broader process-aware WAAM planning principles
5. `fundamentals-metal-additive-manufacturing.md`
   - load general manufacturing background only if needed
6. `part-lessons/<part>.md`
   - read the closest matching part case study

## Structure
- `planner-patterns.md`
  - reusable experimental planning ideas
- `failure-modes.md`
  - recurring baseline failure patterns
- `waam-toolpath-key-points.md`
  - process-aware WAAM toolpath planning synthesis
- `fundamentals-metal-additive-manufacturing.md`
  - broader background reference
- `part-lessons/`
  - one file per part / geometry family
- `part-lessons/_template.md`
  - template for starting a new part lesson file

## Working rule
For each new part:
1. create a note in `part-lessons/` using `part-lessons/_template.md`
2. record geometry type, orientation assumptions, topology zones, baseline failure, experimental fix, and result
3. when a lesson generalizes, move the distilled rule into `planner-patterns.md` or `failure-modes.md`
4. keep file names short, stable, lowercase, and geometry-specific

## Authoring rules for AI-friendly notes
- keep headings consistent across part files
- prefer short bullet points over long prose
- separate observations from conclusions
- state uncertainty explicitly (`confirmed`, `suspected`, `needs verification`)
- record what was tried, what failed, what improved, and what remains open
- write reusable lessons in general terms so they can transfer to future parts
- avoid burying the main lesson deep in narrative text

## Boundary
- Production logic belongs in the main WAAM pipeline.
- Part-specific iteration belongs in `Waam_tech/experimental/`.
- Reusable lessons belong in this folder.

## Current case studies
- `part-lessons/engineblock.md`
- `part-lessons/pieza-upna.md`
- `part-lessons/studycadcam-78.md`
