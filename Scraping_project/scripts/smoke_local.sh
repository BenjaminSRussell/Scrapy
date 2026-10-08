#!/usr/bin/env bash
# Local contributor smoke check (#323). Non-interactive; exits non-zero on failure.
#
#   bash scripts/smoke_local.sh              # env checks + a fast offline test subset
#   bash scripts/smoke_local.sh --no-tests   # env checks only (used by the devcontainer)
#   SMOKE_TESTS="tests/unit/test_cli.py" bash scripts/smoke_local.sh   # custom subset
#
# Checks: Python >= 3.11, core imports, Redis reachability (optional: warns only),
# Playwright browser (optional: warns only), then the pytest subset.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PY="${PYTHON:-python3}"
if [ -z "${PYTHON:-}" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
RUN_TESTS=1
[ "${1:-}" = "--no-tests" ] && RUN_TESTS=0

fail=0
ok()   { echo "  [ok]   $*"; }
warn() { echo "  [warn] $*"; }
bad()  { echo "  [FAIL] $*"; fail=1; }

echo "== Python"
if "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    ok "$("$PY" -V) ($PY)"
else
    bad "$("$PY" -V 2>&1) is older than 3.11 (CI runs 3.11 and 3.12)"
fi

echo "== Core imports"
for mod in scrapy twisted deltalake pyarrow redis pydantic yaml prometheus_client; do
    if "$PY" -c "import $mod" 2>/dev/null; then ok "$mod"; else bad "$mod (pip install -r requirements.txt)"; fi
done
if PYTHONPATH=".:src${PYTHONPATH:+:$PYTHONPATH}" OBS_OFFLINE=1 "$PY" -c "import src.settings" 2>/tmp/smoke_settings.err; then
    ok "src.settings loads"
else
    bad "src.settings failed to import: $(tail -1 /tmp/smoke_settings.err)"
fi

echo "== Optional services"
if "$PY" - <<'PYEOF' 2>/dev/null
import os, redis
redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")),
            socket_connect_timeout=1).ping()
PYEOF
then ok "Redis reachable"; else warn "Redis not reachable (tests that need it are skipped; 'python start.py' runs it)"; fi
if "$PY" -c "import playwright" 2>/dev/null; then
    if ls ~/.cache/ms-playwright/chromium-* >/dev/null 2>&1; then ok "Playwright chromium installed"
    else warn "Playwright browser missing; only needed for JS-spider work: $PY -m playwright install chromium"; fi
else
    warn "playwright not installed (optional; JS spider only)"
fi

if [ "$RUN_TESTS" = "1" ]; then
    echo "== Fast offline test subset"
    # shellcheck disable=SC2086
    TESTS=${SMOKE_TESTS:-"tests/unit/test_settings_config.py tests/unit/test_cli.py tests/unit/test_docker_entrypoints.py"}
    if OBS_OFFLINE=1 "$PY" -m pytest $TESTS -q -o addopts= -m "not slow and not kafka and not performance" -p no:cacheprovider; then
        ok "tests passed"
    else
        bad "tests failed"
    fi
fi

echo
if [ "$fail" = "0" ]; then echo "SMOKE OK"; else echo "SMOKE FAILED"; fi
exit "$fail"
