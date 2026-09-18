// Pipeline Control Center - Main Application
// Real-time monitoring dashboard for UConn scraping pipeline

const METRICS_URL = 'http://localhost:9090/metrics';
const REFRESH_INTERVAL = 5000;

let countdown = 5;
let charts = {};
let historicalData = {
    timestamps: [],
    urls: [],
    pages: [],
    summaries: [],
    maxDataPoints: 50
};
let previousMetrics = {};
let startTime = Date.now();
let activityLog = [];

let lastSuccessfulFetchAt = null;
const STALE_AFTER_MS = REFRESH_INTERVAL * 3;

function setHealthTile(id, state, label) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('healthy', 'unhealthy', 'unknown');
    if (state === 'healthy' || state === 'unhealthy' || state === 'unknown') {
        el.classList.add(state);
    }
    const valueEl = el.querySelector('.health-value');
    if (valueEl) {
        valueEl.textContent = label;
    }
}

function setSystemStatus(mode, label) {
    const el = document.getElementById('system-status');
    if (!el) return;
    el.classList.remove('online', 'offline');
    el.classList.add(mode === 'online' ? 'online' : 'offline');
    const textSpan = el.querySelector('span:not(.status-dot)');
    if (textSpan) {
        textSpan.textContent = label;
    } else {
        const spans = el.querySelectorAll('span');
        if (spans.length >= 2) {
            spans[spans.length - 1].textContent = label;
        }
    }
}

function setStageBadge(id, text, badgeClass) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = `card-badge ${badgeClass}`;
    el.textContent = text;
}

function setStageBadges(mode, metrics) {
    const badgeIds = [
        'overview-s1-badge',
        'overview-s2-badge',
        'overview-s3-badge',
        'overview-s4-badge'
    ];

    if (mode === 'unknown') {
        badgeIds.forEach(id => setStageBadge(id, 'Unknown', 'badge-unknown'));
        return;
    }

    const running = metrics && metrics['pipeline_running'] === 1;
    const s1 = (metrics && (metrics['stage1_urls_discovered_total'] || 0)) > 0;
    const s2 = (metrics && (metrics['stage2_pages_analyzed_total'] || 0)) > 0;
    const s3 = (metrics && (metrics['stage3_summaries_created_total'] || 0)) > 0;
    const s4 = (metrics && (metrics['stage4_large_doc_summaries_total'] || 0)) > 0;

    if (!running) {
        setStageBadge('overview-s1-badge', 'Offline', 'badge-danger');
        setStageBadge('overview-s2-badge', 'Offline', 'badge-danger');
        setStageBadge('overview-s3-badge', 'Offline', 'badge-danger');
        setStageBadge('overview-s4-badge', 'Offline', 'badge-danger');
        return;
    }

    setStageBadge('overview-s1-badge', s1 ? 'Active' : 'Standby', s1 ? 'badge-info' : 'badge-warning');
    setStageBadge('overview-s2-badge', s2 ? 'Active' : 'Standby', s2 ? 'badge-info' : 'badge-warning');
    setStageBadge('overview-s3-badge', s3 ? 'Running' : 'Standby', s3 ? 'badge-success' : 'badge-warning');
    setStageBadge('overview-s4-badge', s4 ? 'Running' : 'Standby', s4 ? 'badge-success' : 'badge-warning');
}

function showMetricsBanner(kind, message) {
    const banner = document.getElementById('metrics-banner');
    if (!banner) return;
    banner.classList.remove('error', 'stale', 'hidden');
    if (!kind) {
        banner.classList.add('hidden');
        banner.textContent = '';
        return;
    }
    banner.classList.add(kind === 'stale' ? 'stale' : 'error');
    banner.textContent = message || '';
}

function applyMetricsFailure(reason) {
    const reasonText = reason || 'Metrics unreachable';
    let bannerMsg = `Cannot reach metrics: ${reasonText}`;
    if (lastSuccessfulFetchAt) {
        const agoSec = Math.round((Date.now() - lastSuccessfulFetchAt) / 1000);
        const lastOk = new Date(lastSuccessfulFetchAt).toLocaleTimeString();
        if (Date.now() - lastSuccessfulFetchAt > STALE_AFTER_MS) {
            bannerMsg = `Stale — last OK at ${lastOk} (${agoSec}s ago). ${reasonText}`;
            showMetricsBanner('stale', bannerMsg);
        } else {
            showMetricsBanner('error', bannerMsg);
        }
    } else {
        showMetricsBanner('error', bannerMsg);
    }

    setSystemStatus('offline', 'No data');
    setHealthTile('redis-health', 'unknown', 'Unknown');
    setHealthTile('metrics-health', 'unhealthy', 'Unreachable');
    setHealthTile('stage1-health', 'unknown', reasonText);
    setHealthTile('stage2-health', 'unknown', reasonText);
    setHealthTile('stage3-health', 'unknown', reasonText);
    setHealthTile('stage4-health', 'unknown', reasonText);
    setStageBadges('unknown');

    const deltaTables = document.getElementById('delta-tables');
    if (deltaTables) deltaTables.textContent = '—';
    const deltaStatus = document.getElementById('delta-status');
    if (deltaStatus) {
        deltaStatus.textContent = 'Unknown';
        deltaStatus.classList.remove('success');
    }

    const topbar = document.getElementById('topbar-status');
    if (topbar) topbar.textContent = '🔴 NO DATA';
}

function applyMetricsSuccess(metrics) {
    showMetricsBanner(null);

    const running = metrics['pipeline_running'] === 1;
    setSystemStatus(running ? 'online' : 'offline', running ? 'Online' : 'Offline');

    setHealthTile('metrics-health', 'healthy', 'Collecting');

    const hasRedis = ('pipeline_redis_keys' in metrics) || ('pipeline_redis_memory_bytes' in metrics);
    if (hasRedis) {
        setHealthTile('redis-health', 'healthy', 'Healthy');
    } else {
        setHealthTile('redis-health', 'unknown', 'Not reported');
    }

    const stageDefs = [
        { id: 'stage1-health', totalKey: 'stage1_urls_discovered_total', runningLabel: 'Running', idleLabel: 'Quiet' },
        { id: 'stage2-health', totalKey: 'stage2_pages_analyzed_total', runningLabel: 'Running', idleLabel: 'Quiet' },
        { id: 'stage3-health', totalKey: 'stage3_summaries_created_total', runningLabel: 'Running', idleLabel: 'Quiet' },
        { id: 'stage4-health', totalKey: 'stage4_large_doc_summaries_total', runningLabel: 'Running', idleLabel: 'Standby' }
    ];

    stageDefs.forEach(def => {
        if (!running) {
            setHealthTile(def.id, 'unhealthy', 'Offline');
            return;
        }
        const total = metrics[def.totalKey] || 0;
        if (total > 0) {
            setHealthTile(def.id, 'healthy', def.runningLabel);
        } else {
            setHealthTile(def.id, 'healthy', def.idleLabel);
        }
    });

    setStageBadges('from-metrics', metrics);

    const deltaTables = document.getElementById('delta-tables');
    const deltaStatus = document.getElementById('delta-status');
    if (deltaTables) {
        if (metrics['delta_lake_tables'] != null) {
            deltaTables.textContent = String(metrics['delta_lake_tables']);
        } else {
            deltaTables.textContent = '—';
        }
    }
    if (deltaStatus) {
        if (metrics['delta_lake_status'] != null) {
            deltaStatus.textContent = String(metrics['delta_lake_status']);
            deltaStatus.classList.toggle('success', String(metrics['delta_lake_status']).toLowerCase() === 'active');
        } else {
            deltaStatus.textContent = 'Not wired';
            deltaStatus.classList.remove('success');
        }
    }
}

function maybeShowStaleBanner() {
    if (!lastSuccessfulFetchAt) return;
    if (Date.now() - lastSuccessfulFetchAt > STALE_AFTER_MS) {
        const lastOk = new Date(lastSuccessfulFetchAt).toLocaleTimeString();
        const agoSec = Math.round((Date.now() - lastSuccessfulFetchAt) / 1000);
        showMetricsBanner('stale', `Stale — last OK at ${lastOk} (${agoSec}s ago). Metrics may be outdated.`);
    }
}

