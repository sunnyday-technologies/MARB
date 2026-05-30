# Pre-public checklist

This repo is built and the grader runs, but review these before making it public.

## Blocking
- [ ] **Authored-parts license.** The kit zips in `kits/` contain authored STEP
      parts. The component manifest flags several as *"Authored STEP asset. Verify
      upstream license/source terms before public."* Confirm redistribution rights
      for every part (V-Slot / C-Beam / gantry plates / NEMA / wheels / belts)
      before publishing the kits. This is the #1 gate.
- [ ] **Answer-key boundary.** `tasks/m3_crete/` includes the reference STEP + spec
      (fully open, good for reproducibility). With one task that is fine. When a
      second task lands, decide whether to hold out a leaderboard split
      (contamination control, SWE-bench style).

## Cleanup
- [ ] **Local paths in prose.** `results/comparison_claude_tracks.md` and
      `results/prompt_framework_findings.md` still mention local paths
      (`[local-path-redacted] `[session-path-redacted] as provenance. Scrub or keep as you prefer.
      (Code entry points and `marb_runs.json` are already genericized to `runs/`.)
- [ ] **Large file.** `tasks/m3_crete/m3_reference_round1.step` is ~38 MB. Consider
      Git LFS before pushing.
- [ ] **Fresh-env install.** Verify `pip install cadclaw && pip install -r
      requirements.txt` then the grader runs in a clean virtualenv (the graders
      import the `cadclaw` package).

## Publish
- [ ] **GitHub remote.** Recommend creating **private first**:
      `gh repo create sunnyday-technologies/MARB --private --source . --push`,
      review, then flip to public when ready.
- [ ] **DOI.** Mint a Zenodo DOI on first public release and add it to
      `CITATION.cff` + `README.md` (mirrors the CADCLAW citation pattern).

## Confirmed
- Attribution is **Sunnyday Technologies only**; no AI co-authorship anywhere,
  including commit metadata.
- Tested models (Claude, Codex) appear only as benchmark *subjects*, which is
  correct and necessary.
