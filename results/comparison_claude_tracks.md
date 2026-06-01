# ARB head-to-head — first three runs

*Graded 2026-05-26. `grade_native_step.py` (black-box, identical for all) +
`score_report.py` **v0.3** (buildability factor). Drivers: **Claude Opus 4.7**
(Fusion + CadQuery) and **GPT-5 Codex** (CadQuery) — fresh prompt-only sessions
(fairness wall held), same kit, same grader.*

## Result

| | CADCLAW reference *(answer key)* | Claude-CadQuery | Claude-Fusion | GPT-5 Codex (CadQuery) |
|---|---|---|---|---|
| Parts placed | 100 / 100 | 100 / 100 | 100 / 100 | 100 / 100 |
| Inventory / Floating | ✓ / ✓ | ✓ / ✓ | ✓ / ✓ | ✓ / ✓ |
| Interference | ✓ clean | ✗ **35 clips · 248 cm³** | ✗ **44 clips · 323 cm³** | ✗ **50 clips · 288 cm³** |
| Buildability factor | 1.00 | 0.152 | 0.124 | 0.119 |
| **L1 sub-grade** | **100 / 100** | **15.2 / 100** | **12.4 / 100** | **11.9 / 100** |
| **ARB full-stack** | **15.0 / 100** | **6.52 / 100** | **6.24 / 100** | **6.19 / 100** |
| Wall-clock | (resolver) | 48.8 min | 33.7 min | **13.0 min** |
| Attempts / retries / corrections | — | 9 / 2 / 4 | 12 / 1 / 4 | **1 / 0 / 1** |
| Human interventions | — | **0** | **0** | **0** |
| Tokens (billed / output) | — | **22.1M / 321K** | **34.2M / 1.15M** | n/a (Codex) |

Clean L1 = 100 → full-stack 15 (only L0+L1 are gate-verified today). The
buildability factor `1/(1 + clips/10 + overlap_cm³/120)` makes interference gate
the score: a self-intersecting frame cannot be built, so it ranks far below a
clean one even with a perfect BOM.

## What the clips are

Accessories (motors, belts, wheels, pulleys) are skipped, so **every clip is
structural-vs-structural** — real frame errors. Both share the dominant failure:

| Clip pair | Fusion | CadQuery |
|---|---|---|
| C-beam ↔ C-beam (splice joints / post-frame junctions) | 12 | 11 |
| C-beam ↔ 2040 insert (splice insert overlapping its beams) | 10 | 10 |
| C-beam ↔ gantry plate | 8 (~6.1 cm³ ea) | 10 (~1.0–1.4 cm³ ea) |
| C-beam ↔ 2080 lower rail / flat shim | 8 | — |
| other | 4 | 4 |

Both placed spliced members and the centered 2040 inserts **coincident instead of
end-to-end**, and posts/top-frame intersecting. CadQuery's **plate** clips are ~5×
smaller (it extracted real hole patterns); Fusion's are larger but it finished
faster.

## Orientation gap (not yet gated — ARB v-next)

**Neither driver placed the Z-posts in the rotation shown in the reference image**
(channel/face orientation about the vertical axis). The current black-box gates
(inventory / interference / floating) do **not** catch a part that is present,
connected, and non-overlapping but **mis-oriented** — a rotated post still passes
all three. This is a real buildability / review defect and a clear differentiator
the benchmark must capture.

→ **Add an orientation/pose gate** (per-label principal-axis + channel-facing
check vs the reference poses) as the next ARB gate. It would (a) catch the
z-post rotation, (b) rank builds more critically, and (c) reward correct
datum/orientation reasoning. Tracked for ARB v0.9 spec.

## Takeaways

1. **Both AI workflows built a complete, fully-connected ~100-part machine with
   zero human intervention** — real, and the headline.
2. **Neither is buildable yet** — same failure class (gross overlaps at
   structural splices/junctions) plus mis-oriented Z-posts. This is the L1→L2
   gap: they nail inventory + topology but not precise, datum-correct placement.
   The resolver-built reference clears it *by construction* (the L2 capability).
3. **Ranking: Claude-CadQuery (15.2) > Claude-Fusion (12.4) > GPT-5 Codex (11.9)
   — but the _how_ is the headline.** Claude-CadQuery iterated hardest (9
   attempts, re-extracted real hole patterns) → cleanest build, but slowest
   (48.8 min). **GPT-5 Codex one-shot it** (1 attempt, 0 retries) in **13 min** —
   3-4× faster than either Claude run, but the most clips (50) and the lowest
   score. More self-review buys accuracy at the cost of time and tokens; ARB
   makes that trade visible. (GPT token usage not captured by Codex; Claude
   tokens recovered from the run transcripts.)
4. **Effort/autonomy is reported separately from the artifact score** (per the
   runbook): time, attempts, retries, tokens — never folded into the L1 grade.

## Artifacts
- Scores: `results/{fusion_native,claude_cadquery,cadclaw_track_blackbox}_score.json`; reports `*_report.json`.
- Exports: each run folder's `*_native_export.step`.
- Run logs: each run folder's `run_log.yaml`. Token recovery: parsed from the run session transcripts.
- GPT-CadQuery export lands in its run folder → graded on the identical lens.
