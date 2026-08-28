# L4-ECO frozen engineering-change request

ECO request ID: `second-top-cross-spreader-midright-r1`

Starting from the editable source that produced the baseline M3-CRETE
assembly, add exactly one instance of the existing authored asset
`V-Slot 20x40x1000 Linear Rail.step` as a second top-frame cross-spreader.
Do not create replacement geometry.

Place the new rail at the station halfway between the existing
`top_center_spreader` and the existing top-right side rail, measured between
their global-X centers. The new rail must be parallel to
`top_center_spreader`, with its 40 mm dimension vertical and its top face flush
with the top frame. The requested placement tolerance is 0.5 mm.

All 100 existing baseline authored instances are invariants: do not move, remove,
resize, rotate, replace, suppress, or duplicate any of them. The requested
final authored-instance count is 101. Preserve separate, versioned captures of the
editable source and exported STEP before and after the ECO, plus the public
invariant report and the private requested-change grade.

This request defines the desired change, not an answer key or a passing result.
The public AABB gate can test the instance delta, the added rail's rounded
AABB-dimension signature and requested axis orientation, and unchanged baseline
poses; it does not establish source-asset identity or topology. The private
task-specific answer key is still required to test the midpoint/top-flush
placement. MARB does not publish an L4 score until both gates pass and at least
three genuine independent, gradeable ECO runs exist.
