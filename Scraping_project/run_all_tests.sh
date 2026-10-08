#!/usr/bin/env bash
# Non-interactive test runner (#324).
#
# The old interactive "diagnostic suite" called scripts that no longer exist
# (test_single_spider.py, test_start_requests.py, ...) and blocked on `read`.
# This runs the same default selection as CI (.github/workflows/main.yml) from
# the project directory, and passes extra arguments through to pytest:
#
#   ./run_all_tests.sh                    # CI default set
#   ./run_all_tests.sh tests/unit -x      # a subset
#   RUN_ALL_MARKERS=1 ./run_all_tests.sh  # include slow/kafka/performance
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PY="${PYTHON:-python}"
if [ -x .venv/bin/python ] && [ -z "${PYTHON:-}" ]; then
    PY=.venv/bin/python
fi

MARKERS=(-m "not slow and not kafka and not performance")
if [ "${RUN_ALL_MARKERS:-0}" = "1" ]; then
    MARKERS=()
fi

if [ "$#" -eq 0 ]; then
    set -- tests/
fi

echo "Running: $PY -m pytest $* ${MARKERS[*]:+-m \"${MARKERS[1]}\"} -o addopts= -q"
exec "$PY" -m pytest "$@" "${MARKERS[@]}" -o addopts= -q --strict-markers
