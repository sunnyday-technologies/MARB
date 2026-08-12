# Security and benchmark-integrity reporting

MARB is a tool-independent, automatically graded benchmark. Its graders import
CADCLAW's verification gates to score how correctly an AI-assisted tool places
the parts of a real machine.

Because the output is a **published score that people compare tools by**, the
serious defect class here is integrity: anything that lets a submission earn a
result it did not deserve, or that makes the benchmark misreport what it
measured. A grading bug that flatters a submission is, for this project, a
security issue.

## Reporting

Preferred: GitHub's private reporting — **Security → Report a vulnerability** on
<https://github.com/sunnyday-technologies/MARB>.

If you cannot use GitHub, email **security@sunn3d.com**. Please do not open a
public issue for a scoring bypass before it is fixed — a published bypass is a
live cheat for anyone competing.

## In scope

- Any way a submission can influence its own score: writing to grader state,
  reading expected values or goal geometry it was not given, detecting that it
  is being graded, or exploiting a gate's tolerance handling to pass a placement
  that is materially wrong.
- Code execution in the grading harness reachable from a submitted model or
  script, or escape from the environment a submission is evaluated in.
- A published result that does not correspond to the artifacts and grader
  version recorded alongside it, or a leaderboard entry altered after the fact
  rather than superseded.
- Leakage of an unreleased benchmark case or its reference solution.
- Personal data in the repository, submissions, or published results.

## Out of scope

- Disagreement about whether a gate's threshold is the right measure of
  assembly correctness. That is a benchmark-design discussion — open an issue.
- A model simply performing badly, or a grader correctly failing a submission.
- Vulnerabilities in CADCLAW's gates themselves — report those to
  [CADCLAW](https://github.com/sunnyday-technologies/CADCLAW); mention here if
  the impact is specific to grading.

## Response

We aim to acknowledge within five working days. Where a fix changes scoring,
affected results are re-run and the change is stated alongside them; a published
score is corrected in the open rather than quietly adjusted.
