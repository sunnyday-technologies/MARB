# -*- coding: utf-8 -*-
"""Recover token usage for a MARB run from Claude Code session transcripts.

Claude Code does not expose main-loop token totals to the harness, but every
assistant message in its transcript JSONL carries a `usage` block. Summing
those recovers the run's full token bill — the same method used for the
Opus 4.7 first-results cells (`capture_status: recovered_from_transcript`).

Usage:
    python recover_tokens_from_transcripts.py <transcript_dir> [<dir> ...]

Each <transcript_dir> is a Claude Code project folder
(~/.claude/projects/<munged-cwd>/) containing *.jsonl session files; subagent
transcripts in the same folder are included (they are part of the run's bill).

Reports per file and a total:
    input          uncached input tokens
    cache_create   cache-creation input tokens
    cache_read     cache-read input tokens
    output         output tokens
    billed         sum of all four (everything metered at some rate)
"""
import json, sys
from pathlib import Path

KEYS = ("input_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens", "output_tokens")

def scan_file(p):
    tot = dict.fromkeys(KEYS, 0)
    n = 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = (rec.get("message") or {}).get("usage") or rec.get("usage")
            if not isinstance(usage, dict) or "output_tokens" not in usage:
                continue
            n += 1
            for k in KEYS:
                v = usage.get(k)
                if isinstance(v, (int, float)):
                    tot[k] += int(v)
    return tot, n

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for d in sys.argv[1:]:
        d = Path(d)
        files = sorted(d.glob("*.jsonl"))
        print(f"\n== {d}  ({len(files)} transcript files)")
        gtot = dict.fromkeys(KEYS, 0)
        for p in files:
            tot, n = scan_file(p)
            if n == 0:
                continue
            billed = sum(tot.values())
            print(f"  {p.name:48s} msgs={n:5d}  billed={billed:>12,}  output={tot['output_tokens']:>10,}")
            for k in KEYS:
                gtot[k] += tot[k]
        billed = sum(gtot.values())
        print(f"  TOTAL  billed={billed:,}  input={gtot['input_tokens']:,}  "
              f"cache_create={gtot['cache_creation_input_tokens']:,}  "
              f"cache_read={gtot['cache_read_input_tokens']:,}  output={gtot['output_tokens']:,}")

if __name__ == "__main__":
    main()
