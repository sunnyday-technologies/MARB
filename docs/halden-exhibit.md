# Halden Mk-III interactive exhibit

Published route: `/robotic-hand/halden-mkiii/`.

This is the user-supplied procedural 30-part visualization, published as an
unscored exhibit. It is separate from the Amazing Hand benchmark, which uses
29 component designs in 226 occurrences. No runtime, model attribution,
reconstruction score, collision validation or physical performance is inferred
from the demo. The existing benchmark registry and report remain unchanged.

## Artifact and release boundary

- Supplied ZIP SHA-256: `9e11031ef6c480c4877c06b2314b0ba3f43de81a700d82768e1437ba84786caf`.
- The supplied CSS is preserved byte-for-byte. JavaScript has two bounded display
  corrections: a 65-degree camera field of view and explicit PCF shadows.
  `scripts/patch_halden_display.py` reproduces the changes from the exact original
  bundle. Hand geometry and motion logic are unchanged.
- The publication wrapper adds MARB navigation, context, metadata and a
  responsive frame. External font requests are removed; system fallbacks apply.
- This repository reproduces the publication by copying the compiled assets;
  it does not claim to reproduce the original application compilation.
- The ZIP's LinkedIn draft, development files and deployment suggestions are
  outside this website release.
- Third-party notices accompany the bundle. The conservative dependency list
  comes from the supplied lockfile. npm archive hashes are checked before
  collecting notices; supplemental upstream notices identify their pinned source.

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
