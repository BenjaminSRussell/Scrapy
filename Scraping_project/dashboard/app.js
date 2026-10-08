// Pipeline Control Center - Main Application
// Real-time monitoring dashboard for UConn scraping pipeline

// #141: resolved from window.CC_METRICS_URL, ?metrics=, or <this host>:9090 (format-utils.js).
const METRICS_URL = resolveMetricsUrl(
    typeof location !== 'undefined' ? location : null,
    (typeof window !== 'undefined' && window.CC_METRICS_URL) || null
);
const REFRESH_INTERVAL = 5000;
let refreshPaused = false;
let metricsWasDown = false;
let chartsHaveSample = false;

let refreshScheduler = null;
let charts = {};
let historicalData = {
    timestamps: [],
    urls: [],
    pages: [],
    summaries: [],
    sampleTimes: [],  // ms, parallel to the arrays above (#141)
    maxDataPoints: 50
};
let previousMetrics = {};
let previousMetricsAt = 0;  // ms timestamp of previousMetrics (#141)
let startTime = Date.now();
let activityLog = [];
let lastHistoryDayKey = null;

function formatHistoryLabel(d = new Date()) {
    const dayKey = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    const time = d.toLocaleTimeString();
    if (lastHistoryDayKey !== null && lastHistoryDayKey !== dayKey) {
        // Include short date when day changes (midnight boundary)
        const date = d.toLocaleDateString(undefined, { month: '2-digit', day: '2-digit' });
        lastHistoryDayKey = dayKey;
        return `${date} ${time}`;
    }
    lastHistoryDayKey = dayKey;
    return time;
}


function initializeCharts() {
    const chartConfig = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                display: false, // until first real sample (#1076)
                position: 'top'
            },
            tooltip: {
                enabled: false
            }
        },
        scales: {
            y: {
                beginAtZero: true
            }
        }
    };

    charts.throughput = new Chart(document.getElementById('throughput-chart'), {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'URLs/min',
                data: [],
                borderColor: 'rgb(37, 99, 235)',
                backgroundColor: 'rgba(37, 99, 235, 0.1)',
                tension: 0.4
            }, {
                label: 'Pages/min',
                data: [],
                borderColor: 'rgb(124, 58, 237)',
                backgroundColor: 'rgba(124, 58, 237, 0.1)',
                tension: 0.4
            }]
        },
        options: chartConfig
    });

    charts.stageProgression = new Chart(document.getElementById('stage-progression-chart'), {
        type: 'bar',
        data: {
            labels: ['Stage 1', 'Stage 2', 'Stage 3', 'Stage 4'],
            datasets: [{
                label: 'Documents Processed',
                data: [0, 0, 0, 0],
                backgroundColor: [
                    'rgba(37, 99, 235, 0.7)',
                    'rgba(124, 58, 237, 0.7)',
                    'rgba(16, 185, 129, 0.7)',
                    'rgba(245, 158, 11, 0.7)'
                ],
                borderColor: [
                    'rgb(37, 99, 235)',
                    'rgb(124, 58, 237)',
                    'rgb(16, 185, 129)',
                    'rgb(245, 158, 11)'
                ],
                borderWidth: 2
            }]
        },
        options: chartConfig
    });

    charts.routing = new Chart(document.getElementById('routing-chart'), {
        type: 'doughnut',
        data: {
            labels: ['Quality Docs (Stage 3)', 'Massive Docs (Stage 4)'],
            datasets: [{
                data: [0, 0],
                backgroundColor: [
                    'rgba(16, 185, 129, 0.7)',
                    'rgba(245, 158, 11, 0.7)'
                ],
                borderColor: [
                    'rgb(16, 185, 129)',
                    'rgb(245, 158, 11)'
                ],
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            onResize: (chart, size) => applyDoughnutLegend(chart, size.width),
            plugins: {
                legend: {
                    display: false,
                    position: 'bottom'
                },
                tooltip: { enabled: false }
            }
        }
    });
    applyDoughnutLegend(charts.routing, charts.routing.width);

    charts.urls = new Chart(document.getElementById('urls-chart'), {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'URLs Discovered',
                data: [],
                borderColor: 'rgb(37, 99, 235)',
                backgroundColor: 'rgba(37, 99, 235, 0.1)',
                fill: true,
                tension: 0.4
            }]
        },
        options: chartConfig
    });

    charts.pages = new Chart(document.getElementById('pages-chart'), {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Pages Analyzed',
                data: [],
                borderColor: 'rgb(124, 58, 237)',
                backgroundColor: 'rgba(124, 58, 237, 0.1)',
                fill: true,
                tension: 0.4
            }]
        },
        options: chartConfig
    });

    charts.summaries = new Chart(document.getElementById('summaries-chart'), {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Summaries Created',
                data: [],
                borderColor: 'rgb(16, 185, 129)',
                backgroundColor: 'rgba(16, 185, 129, 0.1)',
                fill: true,
                tension: 0.4
            }]
        },
        options: chartConfig
    });
}

// parseMetrics / formatNumber / formatBytes come from format-utils.js (#1043)

function getUptime() {
    const seconds = Math.floor((Date.now() - startTime) / 1000);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    if (hours > 0) {
        return `${hours}h ${minutes}m`;
    } else if (minutes > 0) {
        return `${minutes}m ${secs}s`;
    }
    return `${secs}s`;
}


function safeLinkify(escapedText) {
    // Input must already be HTML-escaped. Only promote http(s) URLs.
    return String(escapedText).replace(
        /https?:\/\/[^\s<]+/gi,
        (url) => {
            if (/^javascript:/i.test(url)) return url;
            const href = url.replace(/"/g, '&quot;');
            return `<a href="${href}" target="_blank" rel="noopener noreferrer">${url}</a>`;
        }
    );
}

function escapeHtml(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function addActivityLogItem(type, message) {
    const timestamp = new Date().toLocaleTimeString();
    activityLog.unshift({ type, message, timestamp });

    if (activityLog.length > 50) {
        activityLog.pop();
    }

    updateActivityLog();
}

let activityPinned = false;
document.addEventListener('DOMContentLoaded', () => {
    ['activity-log', 'overview-activity'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('scroll', () => {
            activityPinned = el.scrollTop + el.clientHeight < el.scrollHeight - 8;
        });
    });
});


function renderActivityList(container, { maxItems = 50 } = {}) {
    if (!container) return;
    const items = activityLog.slice(0, maxItems);
    container.innerHTML = items.map(item => `
        <li class="activity-item ${escapeHtml(item.type)}">
            <div class="activity-timestamp">${escapeHtml(item.timestamp)}</div>
            <div class="activity-message">${safeLinkify(escapeHtml(item.message))}</div>
        </li>
    `).join('');
}

function updateActivityLog() {

    const logContainers = [
        document.getElementById('overview-activity'),
        document.getElementById('activity-log')
    ];

    logContainers.forEach(container => {
        if (!container) return;
        if (activityPinned && container.id === "activity-log") return;
        const maxItems = container.id === 'overview-activity' ? 8 : 50;
        renderActivityList(container, { maxItems });
    });
}

function detectSignificantChanges(metrics) {
    const prev = previousMetrics;

    if (metrics['stage1_urls_discovered_total'] > prev['stage1_urls_discovered_total']) {
        const newUrls = metrics['stage1_urls_discovered_total'] - prev['stage1_urls_discovered_total'];
        addActivityLogItem('success', `Stage 1: Discovered ${newUrls} new URL(s)`);
    }

    if (metrics['stage2_pages_analyzed_total'] > prev['stage2_pages_analyzed_total']) {
        const newPages = metrics['stage2_pages_analyzed_total'] - prev['stage2_pages_analyzed_total'];
        addActivityLogItem('success', `Stage 2: Analyzed ${newPages} new page(s)`);
    }

    if (metrics['stage3_summaries_created_total'] > prev['stage3_summaries_created_total']) {
        const newSummaries = metrics['stage3_summaries_created_total'] - prev['stage3_summaries_created_total'];
        addActivityLogItem('success', `Stage 3: Created ${newSummaries} new summary(ies)`);
    }

    if (metrics['stage4_large_doc_summaries_total'] > prev['stage4_large_doc_summaries_total']) {
        const newLarge = metrics['stage4_large_doc_summaries_total'] - prev['stage4_large_doc_summaries_total'];
        addActivityLogItem('success', `Stage 4: Processed ${newLarge} large document(s)`);
    }

    if (metrics['stage3_documents_deduplicated_total'] > prev['stage3_documents_deduplicated_total']) {
        const dedupCount = metrics['stage3_documents_deduplicated_total'] - prev['stage3_documents_deduplicated_total'];
        addActivityLogItem('warning', `Stage 3: Deduplicated ${dedupCount} document(s)`);
    }
}

function updateHistoricalData(metrics) {
    const now = new Date();
    const timeLabel = now.toLocaleTimeString();

    historicalData.timestamps.push(timeLabel);
    historicalData.urls.push(metrics['stage1_urls_discovered_total'] || 0);
    historicalData.pages.push(metrics['stage2_pages_analyzed_total'] || 0);
    historicalData.summaries.push(metrics['stage3_summaries_created_total'] || 0);
    historicalData.sampleTimes.push(now.getTime());

    if (historicalData.timestamps.length > historicalData.maxDataPoints) {
        historicalData.timestamps.shift();
        historicalData.urls.shift();
        historicalData.pages.shift();
        historicalData.summaries.shift();
        historicalData.sampleTimes.shift();
    }

    updatePerformanceCharts();
    // Leave the cold-chart state (#1076) on the first real sample. The helper
    // existed but was never called, so legends/tooltips stayed off and the
    // "Waiting for first metrics sample" placeholder never cleared (#977).
    markChartsHaveSample();
}

function updatePerformanceCharts() {
    if (charts.urls) {
        charts.urls.data.labels = historicalData.timestamps;
        charts.urls.data.datasets[0].data = historicalData.urls;
        charts.urls.update('none');
    }

    if (charts.pages) {
        charts.pages.data.labels = historicalData.timestamps;
        charts.pages.data.datasets[0].data = historicalData.pages;
        charts.pages.update('none');
    }

    if (charts.summaries) {
        charts.summaries.data.labels = historicalData.timestamps;
        charts.summaries.data.datasets[0].data = historicalData.summaries;
        charts.summaries.update('none');
    }

    if (charts.throughput && historicalData.timestamps.length > 1) {
        const urlsRate = [];
        const pagesRate = [];

        for (let i = 1; i < historicalData.urls.length; i++) {
            const ms = historicalData.sampleTimes[i] - historicalData.sampleTimes[i-1];
            urlsRate.push(ratePerMinute(historicalData.urls[i], historicalData.urls[i-1], ms));
            pagesRate.push(ratePerMinute(historicalData.pages[i], historicalData.pages[i-1], ms));
        }

        charts.throughput.data.labels = historicalData.timestamps.slice(1);
        charts.throughput.data.datasets[0].data = urlsRate;
        charts.throughput.data.datasets[1].data = pagesRate;
        charts.throughput.update('none');
    }
    syncAllChartTables();
    updateOverviewSparklines();
}

function calculateRates(metrics, now = Date.now()) {
    // #141: all rates are per minute, from the actual time since the last sample.
    const prev = previousMetrics;
    const elapsedMs = previousMetricsAt ? now - previousMetricsAt : 0;
    const rates = { urls: 0, pages: 0, summaries: 0, largeDocs: 0 };
    if (Object.keys(prev).length > 0) {
        rates.urls = ratePerMinute(metrics['stage1_urls_discovered_total'], prev['stage1_urls_discovered_total'], elapsedMs);
        rates.pages = ratePerMinute(metrics['stage2_pages_analyzed_total'], prev['stage2_pages_analyzed_total'], elapsedMs);
        rates.summaries = ratePerMinute(metrics['stage3_summaries_created_total'], prev['stage3_summaries_created_total'], elapsedMs);
        rates.largeDocs = ratePerMinute(metrics['stage4_large_doc_summaries_total'], prev['stage4_large_doc_summaries_total'], elapsedMs);
    }
    return rates;
}


/** Announce System Health tile transitions once per change (#1096). */
const _healthLastState = Object.create(null);

function setHealthTile(id, label, healthy, detailText) {
    const el = document.getElementById(id);
    if (!el) return;
    const state = healthy ? 'healthy' : 'unhealthy';
    const value = detailText || (healthy ? 'Healthy' : 'Unhealthy');
    const prev = _healthLastState[id];
    el.classList.toggle('healthy', healthy);
    el.classList.toggle('unhealthy', !healthy);
    const valueEl = el.querySelector('.health-value');
    if (valueEl) valueEl.textContent = value;
    if (prev === state + '|' + value) return;
    _healthLastState[id] = state + '|' + value;
    const live = document.getElementById('health-announce');
    if (live) {
        live.textContent = `${label}: ${value}`;
    }
}


function setMetricText(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    const next = String(value);
    if (el.textContent !== next) {
        el.textContent = next;
        el.classList.remove('flash');
        // reflow so animation restarts
        void el.offsetWidth;
        el.classList.add('flash');
    }
}

const MILESTONES = [1000, 10000, 100000, 1000000];
const milestonesHit = {};
let milestonesPrimed = false;

function checkMilestones(metrics) {
    const tracked = {
        stage1_urls_discovered_total: 'URLs discovered',
        stage2_pages_analyzed_total: 'pages analyzed',
    };
    for (const [key, label] of Object.entries(tracked)) {
        const value = metrics[key] || 0;
        for (const m of MILESTONES) {
            const id = key + ':' + m;
            if (value >= m && !milestonesHit[id]) {
                milestonesHit[id] = true;
                // Milestones already passed on first load are recorded silently.
                if (milestonesPrimed) {
                    addActivityLogItem('success', `Milestone: ${formatNumber(m)} ${label}`);
                }
            }
        }
    }
    milestonesPrimed = true;
}

function updateDashboard(metrics) {
    checkMilestones(metrics);
    if (metricsWasDown) {
        metricsWasDown = false;
        const ann = document.getElementById('metrics-reconnect-announce') || document.getElementById('refresh-status');
        if (ann) ann.textContent = 'Metrics connection restored';
        addActivityLogItem('success', 'Metrics connection restored');
    }
    const s1Discovered = metrics['stage1_urls_discovered_total'] || 0;
    const s1Queued = metrics['stage1_urls_queued_total'] || 0;
    const s2Analyzed = metrics['stage2_pages_analyzed_total'] || 0;
    const s2Quality = metrics['stage2_quality_docs_total'] || 0;
    const s2Massive = metrics['stage2_massive_docs_total'] || 0;
    const s2Words = metrics['stage2_avg_word_count'] || 0;
    const s3Summaries = metrics['stage3_summaries_created_total'] || 0;
    const s3Dedup = metrics['stage3_documents_deduplicated_total'] || 0;
    const s4Summaries = metrics['stage4_large_doc_summaries_total'] || 0;
    const s4Compression = metrics['stage4_avg_compression_ratio'] || 0;

    const rates = calculateRates(metrics);

    document.getElementById('topbar-status').textContent = metrics['pipeline_running'] === 1 ? '🟢 ONLINE' : '🔴 OFFLINE';
    setMetricText('topbar-urls', formatNumber(s1Discovered));
    setMetricText('topbar-summaries', formatNumber(s3Summaries));

    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s1-discovered`);
        if (elem) { const __n = formatNumber(s1Discovered); if (elem.textContent !== String(__n)) { elem.textContent = __n; elem.classList.remove('flash'); void elem.offsetWidth; elem.classList.add('flash'); } else { elem.textContent = __n; } }
    });
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s1-queued`);
        if (elem) { const __n = formatNumber(s1Queued); if (elem.textContent !== String(__n)) { elem.textContent = __n; elem.classList.remove('flash'); void elem.offsetWidth; elem.classList.add('flash'); } else { elem.textContent = __n; } }
    });
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s2-analyzed`);
        if (elem) { const __n = formatNumber(s2Analyzed); if (elem.textContent !== String(__n)) { elem.textContent = __n; elem.classList.remove('flash'); void elem.offsetWidth; elem.classList.add('flash'); } else { elem.textContent = __n; } }
    });
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s2-quality`);
        if (elem) { const __n = formatNumber(s2Quality); if (elem.textContent !== String(__n)) { elem.textContent = __n; elem.classList.remove('flash'); void elem.offsetWidth; elem.classList.add('flash'); } else { elem.textContent = __n; } }
    });

    const s2MassiveElem = document.getElementById('pipeline-s2-massive');
    if (s2MassiveElem) { const __n = formatNumber(s2Massive); if (s2MassiveElem.textContent !== String(__n)) { s2MassiveElem.textContent = __n; s2MassiveElem.classList.remove('flash'); void s2MassiveElem.offsetWidth; s2MassiveElem.classList.add('flash'); } else { s2MassiveElem.textContent = __n; } }

    const s2WordsElem = document.getElementById('pipeline-s2-words');
    if (s2WordsElem) { const __n = formatNumber(s2Words); if (s2WordsElem.textContent !== String(__n)) { s2WordsElem.textContent = __n; s2WordsElem.classList.remove('flash'); void s2WordsElem.offsetWidth; s2WordsElem.classList.add('flash'); } else { s2WordsElem.textContent = __n; } }

    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s3-summaries`);
        if (elem) { const __n = formatNumber(s3Summaries); if (elem.textContent !== String(__n)) { elem.textContent = __n; elem.classList.remove('flash'); void elem.offsetWidth; elem.classList.add('flash'); } else { elem.textContent = __n; } }
    });
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s3-dedup`);
        if (elem) { const __n = formatNumber(s3Dedup); if (elem.textContent !== String(__n)) { elem.textContent = __n; elem.classList.remove('flash'); void elem.offsetWidth; elem.classList.add('flash'); } else { elem.textContent = __n; } }
    });
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s4-summaries`);
        if (elem) { const __n = formatNumber(s4Summaries); if (elem.textContent !== String(__n)) { elem.textContent = __n; elem.classList.remove('flash'); void elem.offsetWidth; elem.classList.add('flash'); } else { elem.textContent = __n; } }
    });

    const compressionRatio = s4Compression > 0 ? (1 / s4Compression).toFixed(0) + 'x' : '0x';
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s4-compression`);
        if (elem) { const __n = compressionRatio; if (elem.textContent !== String(__n)) { elem.textContent = __n; elem.classList.remove('flash'); void elem.offsetWidth; elem.classList.add('flash'); } else { elem.textContent = __n; } }
    });

    const s3RateElem = document.getElementById('pipeline-s3-rate');
    if (s3RateElem) { const __n = rates.summaries.toFixed(1) + '/min'; if (s3RateElem.textContent !== String(__n)) { s3RateElem.textContent = __n; s3RateElem.classList.remove('flash'); void s3RateElem.offsetWidth; s3RateElem.classList.add('flash'); } else { s3RateElem.textContent = __n; } }

    const s4RateElem = document.getElementById('pipeline-s4-rate');
    if (s4RateElem) { const __n = rates.largeDocs.toFixed(1) + '/min'; if (s4RateElem.textContent !== String(__n)) { s4RateElem.textContent = __n; s4RateElem.classList.remove('flash'); void s4RateElem.offsetWidth; s4RateElem.classList.add('flash'); } else { s4RateElem.textContent = __n; } }

    setMetricText('perf-s1-rate', rates.urls.toFixed(1) + ' URLs/min');
    setMetricText('perf-s2-rate', rates.pages.toFixed(1) + ' pages/min');
    setMetricText('perf-s3-rate', rates.summaries.toFixed(1) + ' summaries/min');
    setMetricText('perf-s4-rate', rates.largeDocs.toFixed(1) + ' docs/min');

    const redisKeys = metrics['pipeline_redis_keys'] || 0;
    const redisMemory = metrics['pipeline_redis_memory_bytes'] || 0;
    setMetricText('redis-keys', formatNumber(redisKeys));
    setMetricText('redis-memory', formatBytes(redisMemory));

    // System Health tiles (#1096)
    const redisReported = ('pipeline_redis_keys' in metrics) || ('pipeline_redis_memory_bytes' in metrics);
    setHealthTile('redis-health', 'Redis', redisReported, redisReported ? 'Healthy' : 'Unreachable');
    const metricsOk = Boolean(metrics) && Object.keys(metrics).length > 0;
    setHealthTile('metrics-health', 'Metrics', metricsOk, metricsOk ? 'Collecting' : 'Stale');

    document.getElementById('last-update').textContent = formatEpochTime(metrics['pipeline_last_update_timestamp']);

    // #389/#393: these tiles used to be hardcoded (12 tables, 8 scouts). Show
    // a real value when the exporter reports one, otherwise an explicit dash.
    setReportedValue('delta-table-count', countLabelValues(metrics, 'delta_lake_records', 'table'));
    setReportedValue('scout-instances',
        'pipeline_scout_instances' in metrics ? metrics['pipeline_scout_instances'] : null);
    document.getElementById('last-refresh-time').textContent = new Date().toLocaleTimeString();
    document.getElementById('uptime').textContent = getUptime();

    if (charts.stageProgression) {
        charts.stageProgression.data.datasets[0].data = [s1Discovered, s2Analyzed, s3Summaries, s4Summaries];
        charts.stageProgression.update('none');
    }

    if (charts.routing) {
        charts.routing.data.datasets[0].data = [s2Quality, s2Massive];
        charts.routing.update('none');
        syncAllChartTables();
    }

    if (Object.keys(previousMetrics).length > 0) {
        detectSignificantChanges(metrics);
    }

    updateHistoricalData(metrics);

    previousMetrics = { ...metrics };
    previousMetricsAt = Date.now();
}


function setReportedValue(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    if (value === null || value === undefined) {
        el.textContent = '\u2014';
        el.title = 'Not reported by the metrics exporter';
    } else {
        el.textContent = formatNumber(value);
        el.removeAttribute('title');
    }
}

function setConnectionStatus(kind) {
    // kind: 'online' | 'never' | 'offline'
    const sys = document.getElementById('system-status');
    const top = document.getElementById('topbar-status');
    const labels = {
        online: 'Online',
        never: 'Not connected',
        offline: 'Disconnected',
    };
    const topLabels = {
        online: '● Online',
        never: '○ Not connected',
        offline: '● Disconnected',
    };
    if (sys) {
        sys.classList.remove('online', 'offline', 'never');
        sys.classList.add(kind === 'online' ? 'online' : kind === 'never' ? 'never' : 'offline');
        const span = sys.querySelector('span:last-child');
        if (span) { const __n = labels[kind] || kind; if (span.textContent !== String(__n)) { span.textContent = __n; span.classList.remove('flash'); void span.offsetWidth; span.classList.add('flash'); } else { span.textContent = __n; } }
    }
    if (top) { const __n = topLabels[kind] || kind; if (top.textContent !== String(__n)) { top.textContent = __n; top.classList.remove('flash'); void top.offsetWidth; top.classList.add('flash'); } else { top.textContent = __n; } }
}

async function fetchMetrics() {
    const main = document.getElementById('main') || document.querySelector('.container');
    if (main) main.setAttribute('aria-busy', 'true');
    try {
        const response = await fetch(METRICS_URL);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const text = await response.text();
        const metrics = parseMetrics(text);
        updateDashboard(metrics);
        hasEverSucceeded = true;
        lastMetricsAt = Date.now();
        setConnectionStatus('online');
    } catch (error) {
        metricsWasDown = true;
        console.error('Error fetching metrics:', error);
        addActivityLogItem('danger', `Failed to fetch metrics: ${error.message}`);
        setConnectionStatus(hasEverSucceeded ? 'offline' : 'never');
        document.querySelectorAll('.card-badge.badge-info, .card-badge.badge-success').forEach(b => {
            b.textContent = 'Unknown';
            b.classList.remove('badge-info', 'badge-success');
            b.classList.add('badge-danger');
        });
        document.querySelectorAll('.health-item.healthy').forEach(el => {
            el.classList.remove('healthy');
            el.classList.add('unhealthy');
            const v = el.querySelector('.health-value');
            if (v) v.textContent = 'Unreachable';
        });
        const delta = document.getElementById('delta-tables');
        if (delta) delta.textContent = '—';
        if (typeof setHealthTile === 'function') {
            setHealthTile('metrics-health', 'Metrics', false, 'Fetch failed');
            setHealthTile('redis-health', 'Redis', false, 'Unknown');
        }
    } finally {
        if (main) main.setAttribute('aria-busy', 'false');
    }
}


function downloadBlob(filename, mime, text) {
    const blob = new Blob([text], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    const live = document.getElementById('refresh-status');
    if (live) live.textContent = `Downloaded ${filename}`;
}

function exportActivityLog(format) {
    const rows = activityLog.map(item => ({
        timestamp: item.timestamp || '',
        type: item.type || '',
        message: item.message || ''
    }));
    if (format === 'json') {
        downloadBlob('activity-log.json', 'application/json', JSON.stringify(rows, null, 2));
        return;
    }
    const esc = (v) => `"${String(v).replace(/"/g, '""')}"`;
    const lines = ['timestamp,type,message'];
    for (const r of rows) {
        lines.push([esc(r.timestamp), esc(r.type), esc(r.message)].join(','));
    }
    downloadBlob('activity-log.csv', 'text/csv', lines.join('\n'));
}

function syncChartDataTable(chart, tableId) {
    const table = document.getElementById(tableId);
    if (!table || !chart) return;
    const thead = table.querySelector('thead');
    const tbody = table.querySelector('tbody');
    const labels = chart.data.labels || [];
    const datasets = chart.data.datasets || [];
    thead.innerHTML = `<tr><th scope="col">Label</th>${datasets.map(ds => `<th scope="col">${escapeHtml(ds.label || 'Series')}</th>`).join('')}</tr>`;
    const n = Math.max(labels.length, ...datasets.map(ds => (ds.data || []).length), 0);
    let body = '';
    for (let i = 0; i < n; i++) {
        body += `<tr><th scope="row">${escapeHtml(labels[i] ?? i)}</th>`;
        for (const ds of datasets) {
            const v = (ds.data || [])[i];
            body += `<td>${v == null ? '' : escapeHtml(v)}</td>`;
        }
        body += '</tr>';
    }
    tbody.innerHTML = body;

    const legend = document.getElementById(`${tableId}-legend`);
    if (legend && datasets.length) {
        legend.innerHTML = datasets.map((ds, idx) => {
            const hidden = typeof chart.getDatasetMeta === 'function' && chart.getDatasetMeta(idx)?.hidden;
            const pressed = hidden ? 'false' : 'true';
            return `<button type="button" class="btn" data-dataset-index="${idx}" aria-pressed="${pressed}">${escapeHtml(ds.label || `Series ${idx + 1}`)}</button>`;
        }).join('');
        legend.querySelectorAll('button[data-dataset-index]').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = Number(btn.getAttribute('data-dataset-index'));
                const meta = chart.getDatasetMeta(i);
                meta.hidden = !meta.hidden;
                chart.update();
                btn.setAttribute('aria-pressed', meta.hidden ? 'false' : 'true');
            });
        });
    }
}


function sparkPath(values, w = 100, h = 28) {
    if (!values || values.length < 2) return '';
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    return values.map((v, i) => {
        const x = (i / (values.length - 1)) * w;
        const y = h - ((v - min) / span) * (h - 4) - 2;
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
}

function updateOverviewSparklines() {
    const series = [
        ['overview-s1-discovered', historicalData.urls],
        ['overview-s2-analyzed', historicalData.pages],
        ['overview-s3-summaries', historicalData.summaries],
        ['overview-s4-summaries', historicalData.summaries],
    ];
    for (const [id, values] of series) {
        const svg = document.getElementById(`spark-${id}`);
        const dir = document.getElementById(`spark-dir-${id}`);
        if (!svg) continue;
        if (!values || values.length < 2) {
            svg.innerHTML = '';
            if (dir) dir.textContent = 'Trend: waiting for samples';
            continue;
        }
        const d = sparkPath(values);
        const first = values[0];
        const last = values[values.length - 1];
        const delta = last - first;
        const arrow = delta > 0 ? '▲ rising' : delta < 0 ? '▼ falling' : '● flat';
        svg.innerHTML = `<path d="${d}" fill="none" stroke="currentColor" stroke-width="1.5" />`;
        if (dir) dir.textContent = `Trend: ${arrow} (${delta >= 0 ? '+' : ''}${delta})`;
    }
}

function syncAllChartTables() {
    const map = [
        ['throughput', 'table-throughput'],
        ['stageProgression', 'table-stage'],
        ['routing', 'table-routing'],
        ['urls', 'table-urls'],
        ['pages', 'table-pages'],
        ['summaries', 'table-summaries'],
    ];
    for (const [key, tid] of map) {
        if (charts[key]) syncChartDataTable(charts[key], tid);
    }
}



function resetChartHistory() {
    historicalData.timestamps.length = 0;
    historicalData.urls.length = 0;
    historicalData.pages.length = 0;
    historicalData.summaries.length = 0;
    lastHistoryDayKey = null;
    Object.values(charts || {}).forEach(ch => {
        if (!ch?.data) return;
        ch.data.labels = [];
        (ch.data.datasets || []).forEach(ds => { ds.data = []; });
        try { ch.update('none'); } catch (_) {}
    });
    syncAllChartTables?.();
    document.querySelectorAll('[id^="spark-dir-"]').forEach(el => {
        el.textContent = 'Trend: waiting for samples';
    });
    const live = document.getElementById('refresh-status');
    if (live) live.textContent = 'Chart history reset';
}


function applyDoughnutLegend(chart, width) {
    const legend = chart?.options?.plugins?.legend;
    if (!legend) return;
    const layout = doughnutLegendLayout(width);
    const labels = legend.labels || {};
    const same = legend.position === layout.position && labels.boxWidth === layout.labels.boxWidth;
    if (same) return;
    legend.position = layout.position;
    legend.labels = Object.assign({}, labels, layout.labels);
    try { chart.update('none'); } catch (_) {}
}

function markChartsHaveSample() {
    if (chartsHaveSample) return;
    chartsHaveSample = true;
    Object.values(charts || {}).forEach(ch => {
        if (!ch?.options?.plugins) return;
        if (ch.options.plugins.legend) ch.options.plugins.legend.display = true;
        if (ch.options.plugins.tooltip) ch.options.plugins.tooltip.enabled = true;
        try { ch.update('none'); } catch (_) {}
    });
    document.querySelectorAll('.chart-container.is-empty').forEach(el => el.classList.remove('is-empty'));
    document.querySelectorAll('.chart-empty-placeholder').forEach(el => el.remove());
}

function ensureChartPlaceholders() {
    if (chartsHaveSample) return;
    document.querySelectorAll('.chart-container').forEach(el => {
        el.classList.add('is-empty');
        if (!el.querySelector('.chart-empty-placeholder')) {
            const ph = document.createElement('div');
            ph.className = 'chart-empty-placeholder';
            ph.textContent = 'Waiting for first metrics sample…';
            el.appendChild(ph);
        }
    });
}


function activateTab(tabName, pushUrl = true) {
    const tabButtons = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');
    let found = false;
    tabButtons.forEach(btn => {
        const on = btn.getAttribute('data-tab') === tabName;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
        if (on) found = true;
    });
    if (!found) return false;
    tabContents.forEach(content => {
        const on = content.id === `tab-${tabName}`;
        content.classList.toggle('active', on);
    });
    requestAnimationFrame(() => {
        Object.values(charts || {}).forEach(ch => { try { ch.resize(); } catch (_) {} });
    });
    if (pushUrl) {
        const url = new URL(window.location.href);
        url.searchParams.set('tab', tabName);
        url.hash = tabName;
        history.replaceState(null, '', url);
    }
    return true;
}

function tabFromLocation() {
    const params = new URLSearchParams(window.location.search);
    const q = params.get('tab');
    if (q) return q;
    const h = (window.location.hash || '').replace(/^#/, '');
    return h || null;
}

function setupTabs() {
    const resetHist = document.getElementById('reset-chart-history');
    if (resetHist) resetHist.addEventListener('click', resetChartHistory);

    const clearBtn = document.getElementById('clear-activity');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            const log = document.getElementById('activity-log');
            if (log) log.innerHTML = '';
            if (typeof activityLog !== 'undefined') activityLog.length = 0;
            updateActivityLog();
        });
    }
    const exportJson = document.getElementById('export-activity-json');
    if (exportJson) exportJson.addEventListener('click', () => exportActivityLog('json'));
    const exportCsv = document.getElementById('export-activity-csv');
    if (exportCsv) exportCsv.addEventListener('click', () => exportActivityLog('csv'));

    const tabButtons = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabName = button.getAttribute('data-tab');
            activateTab(tabName, true);
            const panel = document.getElementById(`tab-${tabName}`);
            if (panel) { panel.setAttribute('tabindex', '-1'); panel.focus(); }
        });
    });
}

function formatRelative(ts) {
    if (!ts) return 'never';
    const sec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    return Math.floor(sec / 3600) + 'h ago';
}
let lastMetricsAt = null;
let hasEverSucceeded = false;

// Display only: the countdown is read from the scheduler's nextFetchAt, so it
// reaches 0 exactly when a fetch starts and cannot drift from the poll (#986).
function renderCountdown() {
    if (!refreshScheduler) return;
    const el = document.getElementById('refresh-countdown');
    if (el) {
        const s = refreshScheduler.secondsRemaining();
        const text = s === null ? 'paused' : String(s);
        if (el.textContent !== text) el.textContent = text;
    }
    const rel = document.getElementById('last-updated-rel');
    if (rel) rel.textContent = formatRelative(lastMetricsAt);
}

function startCountdown() {
    renderCountdown();
    setInterval(renderCountdown, 250);
}

// One aria announcement per completed successful fetch (#1093).
async function fetchAndAnnounce() {
    const before = lastMetricsAt;
    await fetchMetrics();
    const live = document.getElementById('refresh-status');
    if (live && lastMetricsAt !== before) live.textContent = 'Metrics refreshed';
    renderCountdown();
}


function applyEnvBadge() {
    const env = (window.__CC_FEATURES__ && window.__CC_FEATURES__.env) || window.__CC_ENV__ || 'local';
    const el = document.getElementById('env-badge');
    if (!el) return;
    el.textContent = env;
    el.className = 'env-badge env-' + String(env).toLowerCase().replace(/[^a-z]/g, '');
    if (/prod/i.test(env)) el.classList.add('env-prod');
}

function applyFeatureFlags() {
    const f = window.__CC_FEATURES__ || {};
    // Hide jobs tab unless flag on (#1047)
    document.querySelectorAll('[data-tab="jobs"], #tab-jobs').forEach(el => {
        el.hidden = !f.jobs;
        if (!f.jobs) el.style.display = 'none';
    });
    if (f.dark === false) {
        document.documentElement.dataset.theme = 'light';
    }
    applyEnvBadge();
}

function applyVersionWatermark() {
    const el = document.getElementById('cc-version');
    if (el) el.textContent = 'v' + (window.__CC_VERSION__ || 'dev');
}

function initialize() {
    console.log('Initializing Pipeline Control Center...');

    applyFeatureFlags();
    applyVersionWatermark();
    setupTabs();
    const deep = tabFromLocation();
    if (deep) activateTab(deep, false);
    initializeCharts();
    ensureChartPlaceholders();
    refreshScheduler = createRefreshScheduler({ interval: REFRESH_INTERVAL, fetch: fetchAndAnnounce });
    startCountdown();

    const pauseBtn = document.getElementById('pause-refresh');
    if (pauseBtn) {
        pauseBtn.addEventListener('click', () => {
            refreshPaused = !refreshPaused;
            pauseBtn.setAttribute('aria-pressed', refreshPaused ? 'true' : 'false');
            pauseBtn.textContent = refreshPaused ? 'Resume' : 'Pause';
            pauseBtn.setAttribute('aria-label', refreshPaused ? 'Resume auto-refresh' : 'Pause auto-refresh');
            refreshScheduler.setPaused(refreshPaused);
            renderCountdown();
            const live = document.getElementById('refresh-status');
            if (live) live.textContent = refreshPaused ? 'Auto-refresh paused' : 'Auto-refresh resumed';
        });
    }
    const manualBtn = document.getElementById('manual-refresh');
    if (manualBtn) manualBtn.addEventListener('click', () => refreshScheduler.refreshNow());

    addActivityLogItem('success', 'Pipeline Control Center initialized');

    refreshScheduler.start();

    const chartHeightMql = window.matchMedia('(max-width: 640px)');
    const onChartBreak = () => {
        requestAnimationFrame(() => {
            Object.values(charts || {}).forEach(ch => { try { ch.resize(); } catch (_) {} });
        });
    };
    if (chartHeightMql.addEventListener) chartHeightMql.addEventListener('change', onChartBreak);
    else if (chartHeightMql.addListener) chartHeightMql.addListener(onChartBreak);

    console.log('Dashboard ready!');
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
} else {
    initialize();
}
