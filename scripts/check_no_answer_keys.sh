#!/bin/sh
# NUL-safe answer-key path/content guard. The Python implementation keeps Git
# path parsing and blob hashing binary-safe on Windows and Linux.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -n "${MARB_PYTHON:-}" ]; then
    exec "$MARB_PYTHON" "$script_dir/check_no_answer_keys.py" "$@"
fi
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$script_dir/check_no_answer_keys.py" "$@"
fi
if command -v python >/dev/null 2>&1; then
    exec python "$script_dir/check_no_answer_keys.py" "$@"
fi
echo "answer-key guard: Python 3 is required" >&2
exit 64
