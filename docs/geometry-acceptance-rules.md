# Geometry acceptance rules

This document records the agreed acceptance rules from the geometry validation discussion. Broader sensor support is outside this work. Terms are defined in [GLOSSARY.md](../GLOSSARY.md).

## Agreed rules

- Extracting a number and establishing model compatibility are separate conclusions. A confidently extracted number does not establish supported geometry.
- Supported geometry requires positive evidence of compatibility with the current model. Relevant descriptions across the instrument record must agree; missing or conflicting evidence leaves support unresolved.
- For this fix, supported geometry requires evidence of a continuous swath centered beneath the satellite, with no gaps or separate pointing needed to reach the stated width. Other understood geometries may run as labeled approximations.
- A known mismatch may be used as an approximation with a clear warning explaining why the model represents the instrument incorrectly. Researcher confirmation of the number does not change the geometry's support classification.
- An understood approximation runs automatically, with its warning included in the results. A separate confirmation step is unnecessary.
- An approximation requires a clear account of what the supplied number represents and what the model simplifies. If that meaning is unclear, ask for clarification before running.
- For separated or discrete tracks with a documented overall span, regardless of track count or span, the warning must explain that the model includes gaps between tracks as accessible. A per-track width does not establish the overall span. The resulting coverage and revisit estimates describe that approximation.
- The approximation label and explanation remain with result summaries, daily and revisit responses, coverage GeoJSON metadata, and follow-up answers.
- If multiple operating modes leave no clear choice of width, ask which mode the study uses. If source values conflict, expose the conflict and ask for clarification. Consider relevant candidates together rather than choosing by their order or ignoring a different kind of geometry measurement.
- Researcher-supplied numbers follow the same evidence and compatibility checks as extracted numbers. A number and citation alone do not bypass those checks; ask for the relevant source description when its meaning is unclear.

## Acceptance examples

- A sourced width with evidence of continuous, nadir-centered coverage and no contradictory description qualifies as supported geometry.
- Six separated laser tracks with a documented 6.6 km overall span may run automatically as an approximation. The warning explains that the model fills the gaps and may overstate access; revisit estimates also describe the simplified model.
- Other separated or discrete tracks may use a documented positive overall span the same way. Their number of tracks and span are source facts; no span is inferred from per-track width.
- An off-nadir description in a different field from the swath number still affects compatibility. The number cannot qualify as supported merely because its own field contains no warning. A run requires either supported geometry or an understood approximation with an explanation.
- A swath number with insufficient description to establish compatibility or explain an approximation requires clarification.
- Distinct 25 km and 100 km operating modes require a mode choice when the study has not specified one. Conflicting widths for the same mode require clarification.
- An angle whose physical meaning is unclear requires clarification, even if the number and its source are certain.
- Entering the separated-track span manually produces the same approximate classification and warning as extracting it from a source.
- Geometry numbers may be extracted from instrument spec fields and actual instrument descriptions, with field provenance. Names, types, and reference URLs provide context but are not numeric geometry sources.
- Mode-specific evidence applies only to the matching mode. Evidence with unknown applicability, negated support, or conflicting assertions requires clarification.

## Result explanation

Approximate results state the input value and source, the difference between the instrument and the model, and the consequence for interpreting modeled access. Warnings explain what is known about the mismatch without inventing an error percentage or promising that an approximation is always an upper or lower bound.

All results continue to describe modeled access at grid-cell centers. They do not establish actual acquisitions or observation of every point within a grid cell.

## Next step

These rules define the implemented behavior. Broader sensor support remains outside this change.
