# Halden Mk-IV interactive exhibit

Published route: `/robotic-hand/halden-mkiii/` (retained for existing links).

This is the user-supplied 59-part hand concept, published as an unscored exhibit.
The inventory includes modeled servos, capstans, tendons, return elastics,
pins and pads; it is not a validated manufacturing bill of materials.
It is separate from the Amazing Hand benchmark, which uses
29 component designs in 226 occurrences. No runtime, model attribution,
reconstruction score, collision validation or physical performance is inferred
from the demo. The existing benchmark registry and report remain unchanged.

## Artifact and release boundary

- The supplied static HTML was exported from the user's Cursor project. The
  displayed revision is `967ba6e`; its SHA-256 is
  `c564cb03c7c7c071246300504b6f3213df3a9c1bbb9685ab244b1d52062a1c66`.
- The embedded CSS is extracted byte-for-byte. JavaScript has four bounded
  corrections: explicit PCF shadows, a 50-degree desktop field of view (mobile
  remains 52 degrees), the label "HAND CONCEPT", and a description
  of modeled components without asserting physical function.
  `scripts/patch_halden_mkiv.py` reproduces extraction and corrections from the
  exact original HTML. Geometry, transforms, pose values and
  motion logic are unchanged.
- The publication wrapper adds MARB navigation, context, metadata and a
  responsive frame. External font requests are removed; system fallbacks apply.
- This repository reproduces the publication by copying the compiled assets;
  it does not claim to reproduce the original application compilation.
- LinkedIn, development files and suggested hardware documentation are outside
  this website release.
- Third-party notices accompany the bundle. The conservative dependency list
  comes from the supplied lockfile. All 66 non-development dependency names,
  versions and integrity hashes match the preceding Mk-III release, so its
  verified notices are retained. npm archive hashes were checked before
  collecting those notices; supplemental upstream notices identify their pinned
  source. The earlier extraction and display script remains as historical
  reproduction support.

## Executable content policy

The existing site remains static except for this exhibit. The release gate
permits exactly one pinned module on this exact page, requires its SHA-256
integrity attribute, and verifies the module bytes. CSP permits only the
reviewed script hashes. It does not enable arbitrary same-origin scripts,
inline executable scripts, eval, connections, workers or remote fonts.

To update the application, review the replacement bundle and notices, update
the pinned hash and SRI together, and repeat release-gate and browser checks.
`tests.test_halden_publication` exercises actual release acceptance and rejection
for a modified bundle, missing SRI, additional scripts, scripts on other pages,
and broader CSP permissions. The test requires PowerShell 7.
