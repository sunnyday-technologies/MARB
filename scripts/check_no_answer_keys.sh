#!/bin/sh
# Fail if any answer-key path is present in the tree or in a commit range.
#
# The MARB answer keys are distributed ONLY via the gated Hugging Face
# dataset (see tasks/m3_crete/ANSWER_KEY.md). Key files must never enter this
# repository's history — not even transiently. This script is the single
# source of truth for the blocked path patterns; both the pre-push hook
# (scripts/hooks/pre-push) and CI (.github/workflows/key-guard.yml) call it.
#
# Usage:
#   check_no_answer_keys.sh --tree                 scan all tracked/staged paths
#   check_no_answer_keys.sh --range <rev-args...>  scan every commit in the range
#                                                  (rev-args passed to git log)
#   check_no_answer_keys.sh --stdin                scan newline-separated paths
#
# Exit codes: 0 clean, 1 key path detected, 64 usage error.
#
# Note: reference_floorplan.png (a kit INPUT, not a key) intentionally does
# not match — the patterns require .step, an assembly/layout/scene spec
# extension, or the ph*_reference key naming.

KEY_PATH_REGEX='(^|/)[^/]*reference[^/]*\.step$|(^|/)[^/]*reference[^/]*assembly[^/]*\.ya?ml$|(^|/)[^/]*reference[^/]*(layout|scene)[^/]*\.(ya?ml|json)$|(^|/)ph[0-9]+_reference|(^|/)[^/]*leak_signature[^/]*\.ya?ml$'

mode="${1:---tree}"
case "$mode" in
  --tree)
    paths=$(git ls-files) || exit 64
    ;;
  --range)
    shift
    [ "$#" -ge 1 ] || { echo "usage: $0 --range <rev-args...>" >&2; exit 64; }
    # --diff-merges=first-parent so an "evil merge" cannot smuggle a key in.
    paths=$(git log --pretty=format: --name-only --diff-merges=first-parent "$@" | sort -u) || exit 64
    ;;
  --stdin)
    paths=$(cat)
    ;;
  *)
    echo "usage: $0 [--tree | --range <rev-args...> | --stdin]" >&2
    exit 64
    ;;
esac

hits=$(printf '%s\n' "$paths" | grep -Ei "$KEY_PATH_REGEX")
if [ -n "$hits" ]; then
  {
    echo ""
    echo "BLOCKED: answer-key path(s) detected:"
    printf '%s\n' "$hits" | sed 's/^/    /'
    echo ""
    echo "MARB answer keys are distributed only via the gated HF dataset and"
    echo "must never be committed here (tasks/m3_crete/ANSWER_KEY.md)."
    echo "Remove the file(s) from the commit(s) and try again."
    echo ""
  } >&2
  exit 1
fi
exit 0
