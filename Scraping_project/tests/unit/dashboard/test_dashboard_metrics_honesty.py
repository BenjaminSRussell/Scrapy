"""
Issue #156: Control Center must not show healthy defaults when metrics fail.
Static honesty checks — no network.
"""
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[3] / "dashboard"
INDEX = DASHBOARD / "index.html"
APP_JS = DASHBOARD / "app.js"


def test_index_has_no_healthy_health_items_in_static_markup():
    html = INDEX.read_text(encoding="utf-8")
    assert "health-item healthy" not in html


def test_index_delta_tables_not_hardcoded_12():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="delta-tables"' in html
    # Value must not be the bare hardcoded 12
    assert 'id="delta-tables">12<' not in html
    assert 'id="delta-tables">—<' in html or 'id="delta-tables">&mdash;<' in html or 'id="delta-tables">—</span>' in html


def test_index_scout_instances_not_bare_8():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="pipeline-s1-scouts"' in html
    # Must not have bare hardcoded 8 as the scout value
    assert ">8</span>" not in html.split("Scout Instances", 1)[1].split("</div>", 1)[0]
    # Placeholder dash present on the scout element
    scout_chunk = html.split('id="pipeline-s1-scouts"', 1)[1].split("</span>", 1)[0]
    assert "—" in scout_chunk or "&mdash;" in scout_chunk
    assert "placeholder until wired" in html


def test_index_metrics_banner_and_manual_refresh_exist():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="metrics-banner"' in html
    assert 'id="manual-refresh"' in html
    assert 'role="alert"' in html


def test_index_overview_badges_default_unknown():
    html = INDEX.read_text(encoding="utf-8")
    for n in range(1, 5):
        assert f'id="overview-s{n}-badge"' in html
    assert html.count(">Unknown</span>") >= 4
    assert "badge-unknown" in html
    # Must not default overview badges to Active/Running as static healthy look
    assert 'id="overview-s1-badge">Active<' not in html
    assert 'id="overview-s3-badge">Running<' not in html


def test_index_system_status_defaults_offline():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="system-status"' in html
    assert "status-indicator offline" in html
    assert "No data" in html


def test_app_js_has_honesty_helpers():
    js = APP_JS.read_text(encoding="utf-8")
    assert "applyMetricsFailure" in js
    assert "applyMetricsSuccess" in js
    assert "STALE_AFTER_MS" in js
    assert "lastSuccessfulFetchAt" in js
    assert "setHealthTile" in js
    assert "setSystemStatus" in js
    assert "setStageBadges" in js
    assert "showMetricsBanner" in js


def test_app_js_fetch_metrics_catch_calls_apply_failure():
    js = APP_JS.read_text(encoding="utf-8")
    # Locate catch block of fetchMetrics
    start = js.index("async function fetchMetrics")
    end = js.index("\n}", start) + 2
    # Find the full function more carefully
    brace = 0
    i = js.index("{", start)
    for j in range(i, len(js)):
        if js[j] == "{":
            brace += 1
        elif js[j] == "}":
            brace -= 1
            if brace == 0:
                body = js[start : j + 1]
                break
    assert "catch" in body
    assert "applyMetricsFailure" in body
    # Ensure #154 XSS helpers remain
    assert "sanitizeActivityType" in js
    assert "resizeChartsForTab" in js
