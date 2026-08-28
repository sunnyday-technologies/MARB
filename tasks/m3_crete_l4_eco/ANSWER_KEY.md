# L4-ECO answer key (private revision ready; gated distribution pending)

The L4-ECO answer key is not tracked in this repository. No task-specific
private reference STEP or placement spec is public. Revision 1 was authored and
validated in the private key repository at commit
`05f0a5f0b027bd9111300fbfe4d06d66f8646a69`.
The files have **not** been uploaded to a gated dataset revision, so external
distribution remains pending and no L4 result is published.

Expected key files:

- `m3_l4_eco_reference_r1.step`
- `m3_l4_eco_reference_assembly_r1.yaml`

## Integrity (sha256)

| Artifact | SHA-256 | Bytes | Status |
|---|---|---:|---|
| reference STEP | `01cf5a71a9fec2889a014c0d5c4329f4fc4fd98dbff4c4a7b12b0ae61fd5f775` | 38,997,683 | private revision validated; gated upload pending |
| placement spec | `095189f0d4c9a2e79c64b0c427a9f6f9470823c5a82610f85e48d8d8be30005d` | 87,605 | private revision validated; gated upload pending |
| build evidence | `19e84a99fd7bdaf84f190b11386ecafc362ad39dcdf480a999af2b9db59ea41c` | 1,875 | private evidence |
| compiler report | `c153ad1f9fc8c6e0d4cd99865ad25a6da409d50e7f3f564eee0d6a72690dbba6` | 123,262 | private evidence |
| self-grade report | `811fddb4bd0b502ba668c4ae81f1b508cfe179f1f6a9286eb3a77eaf3dcef980` | 2,460 | private evidence |

The private snapshot fingerprint is
`146c9446dc1d9e9d2a86e9b81613aaa3a18640817286160f101cc5d794a8cbde`.
Validation recorded all 100 original authored instances unchanged, 101 final
instances with exactly one addition, zero dry-run plan/source compiler fail
findings, a reference self-grade of GAP 0.0 mm / POS absolute 0.0 mm / ORIENT
100%, rejection of 4/4 negative controls, verification of 14/14 key hashes,
and verification of 4/4 evidence-linked hashes. The final reference export
preserves the canonical baseline solids and appends the transformed existing
rail; the public invariant self-check passed with 101 baseline renderable
shapes matched and one addition in a 102-shape changed artifact.

A reference self-grade and negative controls validate the key pipeline; they
are not AI benchmark runs and do not make L4 measured. The key-authoring/team
session accessed private material and cannot count as a blind attempt. The
public invariant gate is also not an answer-key substitute: it does not by
itself establish the requested midpoint/top-flush placement.

The private reference STEP and placement spec remain gitignored. Once an
approved gated dataset revision is uploaded and read back, record that immutable
revision without changing these verified hashes. Store each eligible run's
aggregate requested-change grade report in that same immutable gated revision;
public metadata may attest only its gated path and digest. Publication
validation downloads those exact report bytes with a protected
`MARB_GATED_READ_TOKEN`, checks their digest and aggregate-only schema, and
recomputes the public invariant from the registered STEP pair. No L4 score may
be published until gated distribution, authenticated readback, trusted public
gate recomputation, and the repeat-run evidence contract are satisfied.
