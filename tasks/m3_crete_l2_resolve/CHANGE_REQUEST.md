# L2-RESOLVE frozen change request

Change request ID: `top-spreader-x+200-r1`

Starting from the editable source that produced the initial M3-CRETE assembly,
move the complete top-center-spreader subassembly **+200.0 mm along global X**.
Do not rotate the subassembly and do not rebuild it as replacement geometry.

The affected set is exactly five existing instances:

- `top_center_spreader`
- `top_center_spreader_plate_front`
- `top_center_spreader_plate_front_2`
- `top_center_spreader_plate_back`
- `top_center_spreader_plate_back_2`

The spreader and all four plates must keep their baseline relative transforms
to one another. The other 95 baseline instances are out of scope and must keep
their baseline geometry, orientation, relative placement, and instance count.

Use the same driver session that produced the initial build. Preserve separate,
versioned captures of the editable source and exported STEP before and after the
change. A retry inside that session is not an independent run.

This request defines the desired change, not an answer key or a passing result.
MARB does not publish an L2 score until a task-specific resolver key and at least
three genuine independent, gradeable change-loop runs exist.
