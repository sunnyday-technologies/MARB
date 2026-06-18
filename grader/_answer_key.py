# -*- coding: utf-8 -*-
"""Answer-key guard.

The MARB answer key (the reference STEP + the placement spec) is distributed
through a gated Hugging Face dataset, not committed to this repository. This
helper prints a clear pointer instead of a stack trace when the key is missing,
so a fresh clone fails with instructions rather than a FileNotFoundError.
"""
from __future__ import annotations
import sys
from pathlib import Path

ANSWER_KEY_URL = "https://huggingface.co/datasets/SunnydayTech/marb-m3-crete-answer-key"


def require_answer_key(*paths) -> None:
    """Exit (code 2) with a gated-dataset pointer if any required file is absent."""
    missing = [str(p) for p in paths if p is not None and not Path(p).exists()]
    if not missing:
        return
    sys.stderr.write(
        "\nMARB answer key not found:\n  " + "\n  ".join(missing) + "\n\n"
        "The answer key (reference STEP + placement spec) is gated. Request access at\n"
        f"  {ANSWER_KEY_URL}\n"
        "then place m3_reference_round1.step and m3_reference_assembly.yaml under\n"
        "tasks/m3_crete/ (or pass --ref / --spec pointing at your local copy).\n"
    )
    raise SystemExit(2)
