# MARB blind-kit CAD parts — License and Attribution

The blind kits in this directory (`kits/*.zip`) bundle STEP CAD parts so a model can
build the M3-CRETE assembly. Those parts fall under **two** licenses. This notice does
**not** change the repository's root [`LICENSE`](../LICENSE) (MIT), which governs the
MARB code (harness, grader, figures, docs); it documents the third-party and authored
CAD assets the kits redistribute.

## OpenBuilds-derived parts — CC BY-SA 4.0

The standard motion-system parts are derived from the **OpenBuilds** open-source hardware
library and are redistributed under the
[Creative Commons Attribution-ShareAlike 4.0 International License (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/).

**Original source:** OpenBuilds LLC — https://openbuilds.com/
**License:** CC BY-SA 4.0

Files (in `kits/m3_*_blind_kit*.zip`):

- `C-Beam 40x80x1000 Linear Rail.step`
- `C-Beam Gantry Plate XLarge.STEP`
- `V-Slot 20x40x1000 Linear Rail.step`
- `V-Slot 20x80x1000 Linear Rail.step`
- `V-Slot Gantry Plate 20-80mm.step`
- `Solid V Wheel.step`
- `Smooth Idler Pulley Wheel.step`
- `GT2 Timing Pulley 20 Tooth.step`
- `M3_GT2_belt_Y_958mm.step`
- `M3_GT2_belt_Z_942mm.step`
- `M3_NEMA23_motor.step`
- `VS_Belt_Pinion.step`

If you redistribute or modify these files you must (1) preserve the OpenBuilds attribution,
(2) link to the CC BY-SA 4.0 text, and (3) release modifications under CC BY-SA 4.0 or a
compatible share-alike license.

**Trademarks.** V-Slot®, C-Beam®, and V-Wheel® are registered trademarks of OpenBuilds LLC.
Their use here refers to the geometric profiles published by OpenBuilds under CC BY-SA 4.0 and
does not imply endorsement or affiliation.

## Sunnyday Technologies original parts — repository LICENSE (MIT)

These parts were authored by Sunnyday Technologies and are covered by the repository's root
[`LICENSE`](../LICENSE):

- `M3_6mm_frame_shim_4080.step`
- `ZPMM_6p1_motor_mount_spacer_6mm_holes.step`

(Sunnyday's M3-CRETE project licenses its *custom* hardware under CERN-OHL-W-2.0; if you prefer
these two parts to match that, relicense them to CERN-OHL-W-2.0 — they are Sunnyday's to assign.)

## Provenance

Per-part source/library is recorded in `tasks/m3_crete/m3_component_manifest.yaml`
(`source_library`, `source_path`). Upstream terms mirror
`M3-CRETE/CAD/Components/LICENSE.md`.
