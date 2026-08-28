# L2-RESOLVE answer key (private revision ready; gated distribution pending)

The L2-RESOLVE answer key is not tracked in this repository. Revision 1 was
authored and validated in the private key repository at commit
`d87a58c07c7bf83bb5831e16ce44568527a45057`. The files have **not** been
uploaded to a gated dataset revision, so external distribution remains pending
and no L2 result is published.

Expected key files:

- `m3_l2_resolve_reference_r1.step`
- `m3_l2_resolve_reference_assembly_r1.yaml`

## Integrity (sha256)

The locally validated private revision has these canonical digests:

| File | SHA-256 | Bytes | Status |
|---|---|---:|---|
| `m3_l2_resolve_reference_r1.step` | `f76880eec9fa877accb143f4c7fd2ff0fa13ae42609968d468c6f1043542d7e6` | 38,324,041 | private revision validated; gated upload pending |
| `m3_l2_resolve_reference_assembly_r1.yaml` | `9ff2f99056944197a11d1f2ef6a34fc86b1403ba7ca325370a997520d407119f` | 88,663 | private revision validated; gated upload pending |

Private build evidence is 1,395 bytes with SHA-256
`f8d6fdba92655e69ad280614e4c1d2ed98800a9c78d87bd53950ad70c2494b20`.
The reference self-grade recorded GAP 0.0 mm, POS absolute 0.0 mm, ORIENT
100%, and zero compiler fail findings. A reference self-grade validates the key
pipeline; it is not an AI benchmark run and does not make L2 measured.

The STEP and placement spec remain gitignored and must not be committed. Once a
gated dataset revision is uploaded and read back, record that immutable revision
here without changing these hashes. This gate controls contamination; it is not
a claim that the key is confidential or that a single geometric answer covers
every functional topology.
