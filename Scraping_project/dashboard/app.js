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

function initializeCharts() {
    const chartConfig = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                display: true,
                position: 'top'
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
            plugins: {
                legend: {
                    position: 'bottom'
                }
            }
        }
    });

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

function parseMetrics(text) {
    const metrics = {};
    const lines = text.split('\n');

    for (const line of lines) {
        if (line.startsWith('#') || line.trim() === '') continue;

        const parts = line.split(' ');
        if (parts.length >= 2) {
            const key = parts[0];
            const value = parseFloat(parts[1]);
            if (!isNaN(value)) {
                metrics[key] = value;
            }
        }
    }

    return metrics;
}

function formatNumber(num) {
    if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    } else if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return Math.round(num).toLocaleString();
}

function formatBytes(bytes) {
    if (bytes >= 1073741824) {
        return (bytes / 1073741824).toFixed(2) + ' GB';
    } else if (bytes >= 1048576) {
        return (bytes / 1048576).toFixed(2) + ' MB';
    } else if (bytes >= 1024) {
        return (bytes / 1024).toFixed(2) + ' KB';
    }
    return bytes + ' B';
}

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

function updateActivityLog() {

    const logContainers = [
        document.getElementById('overview-activity'),
        document.getElementById('activity-log')
    ];

    logContainers.forEach(container => {
        if (!container) return;

        if (activityPinned && container.id === "activity-log") return;
        container.innerHTML = activityLog.map(item => `
            <li class="activity-item ${item.type}">
                <div class="activity-timestamp">${item.timestamp}</div>
                <div class="activity-message">${item.message}</div>
            </li>
        `).join('');
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

    if (historicalData.timestamps.length > historicalData.maxDataPoints) {
        historicalData.timestamps.shift();
        historicalData.urls.shift();
        historicalData.pages.shift();
        historicalData.summaries.shift();
    }

    updatePerformanceCharts();
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
            urlsRate.push((historicalData.urls[i] - historicalData.urls[i-1]) * 12);
            pagesRate.push((historicalData.pages[i] - historicalData.pages[i-1]) * 12);
        }

        charts.throughput.data.labels = historicalData.timestamps.slice(1);
        charts.throughput.data.datasets[0].data = urlsRate;
        charts.throughput.data.datasets[1].data = pagesRate;
        charts.throughput.update('none');
    }
}

function calculateRates(metrics) {
    const prev = previousMetrics;
    const timeElapsed = 5;

    const rates = {
        urls: 0,
        pages: 0,
        summaries: 0,
        largeDocs: 0
    };

    if (Object.keys(prev).length > 0) {
        rates.urls = ((metrics['stage1_urls_discovered_total'] - prev['stage1_urls_discovered_total']) / timeElapsed) * 60;
        rates.pages = (metrics['stage2_pages_analyzed_total'] - prev['stage2_pages_analyzed_total']) / timeElapsed;
        rates.summaries = (metrics['stage3_summaries_created_total'] - prev['stage3_summaries_created_total']) / timeElapsed;
        rates.largeDocs = (metrics['stage4_large_doc_summaries_total'] - prev['stage4_large_doc_summaries_total']) / timeElapsed;
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

function updateDashboard(metrics) {
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
    if (s3RateElem) { const __n = rates.summaries.toFixed(1) + '/s'; if (s3RateElem.textContent !== String(__n)) { s3RateElem.textContent = __n; s3RateElem.classList.remove('flash'); void s3RateElem.offsetWidth; s3RateElem.classList.add('flash'); } else { s3RateElem.textContent = __n; } }

    const s4RateElem = document.getElementById('pipeline-s4-rate');
    if (s4RateElem) { const __n = rates.largeDocs.toFixed(1) + '/s'; if (s4RateElem.textContent !== String(__n)) { s4RateElem.textContent = __n; s4RateElem.classList.remove('flash'); void s4RateElem.offsetWidth; s4RateElem.classList.add('flash'); } else { s4RateElem.textContent = __n; } }

    setMetricText('perf-s1-rate', rates.urls.toFixed(1) + ' URLs/min');
    setMetricText('perf-s2-rate', rates.pages.toFixed(2) + ' pages/sec');
    setMetricText('perf-s3-rate', rates.summaries.toFixed(2) + ' summaries/sec');
    setMetricText('perf-s4-rate', rates.largeDocs.toFixed(2) + ' docs/sec');

    const redisKeys = metrics['pipeline_redis_keys'] || 0;
    const redisMemory = metrics['pipeline_redis_memory_bytes'] || 0;
    setMetricText('redis-keys', formatNumber(redisKeys));
    setMetricText('redis-memory', formatBytes(redisMemory));

    // System Health tiles (#1096)
    const redisReported = ('pipeline_redis_keys' in metrics) || ('pipeline_redis_memory_bytes' in metrics);
    setHealthTile('redis-health', 'Redis', redisReported, redisReported ? 'Healthy' : 'Unreachable');
    const metricsOk = Boolean(metrics) && Object.keys(metrics).length > 0;
    setHealthTile('metrics-health', 'Metrics', metricsOk, metricsOk ? 'Collecting' : 'Stale');

    const lastUpdate = new Date(metrics['pipeline_last_update_timestamp'] * 1000);
    document.getElementById('last-update').textContent = lastUpdate.toLocaleTimeString();
    document.getElementById('last-refresh-time').textContent = new Date().toLocaleTimeString();
    document.getElementById('uptime').textContent = getUptime();

    if (charts.stageProgression) {
        charts.stageProgression.data.datasets[0].data = [s1Discovered, s2Analyzed, s3Summaries, s4Summaries];
        charts.stageProgression.update('none');
    }

    if (charts.routing) {
        charts.routing.data.datasets[0].data = [s2Quality, s2Massive];
        charts.routing.update('none');
    }

    if (Object.keys(previousMetrics).length > 0) {
        detectSignificantChanges(metrics);
    }

    updateHistoricalData(metrics);

    previousMetrics = { ...metrics };
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

        countdown = 5;
    } catch (error) {
        console.error('Error fetching metrics:', error);
        addActivityLogItem('danger', `Failed to fetch metrics: ${error.message}`);
        setConnectionStatus(hasEverSucceeded ? 'offline' : 'never');
        if (typeof setHealthTile === 'function') {
            setHealthTile('metrics-health', 'Metrics', false, 'Fetch failed');
            setHealthTile('redis-health', 'Redis', false, 'Unknown');
        }
    } finally {
        if (main) main.setAttribute('aria-busy', 'false');
    }
}

function setupTabs() {
    const clearBtn = document.getElementById('clear-activity');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            const log = document.getElementById('activity-log');
            if (log) log.innerHTML = '';
            if (typeof activityLog !== 'undefined') activityLog.length = 0;
        });
    }

    const tabButtons = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabName = button.getAttribute('data-tab');

            tabButtons.forEach(btn => btn.classList.remove('active'));
            tabContents.forEach(content => content.classList.remove('active'));

            button.classList.add('active');
            button.setAttribute('aria-selected', 'true');
            tabButtons.forEach(btn => { if (btn !== button) btn.setAttribute('aria-selected', 'false'); });
            const panel = document.getElementById(`tab-${tabName}`);
            panel.classList.add('active');
            panel.setAttribute('tabindex', '-1');
            panel.focus();
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

function startCountdown() {

    let lastAnnounced = null;
    setInterval(() => {
        countdown--;
        if (countdown <= 0) {
            countdown = 5;
        }
        const el = document.getElementById('refresh-countdown');
        if (el) el.textContent = countdown;
        const rel = document.getElementById('last-updated-rel');
        if (rel) rel.textContent = formatRelative(lastMetricsAt);
        // Throttle aria announcements to each full cycle reset (#1093)
        const live = document.getElementById('refresh-status');
        if (live && countdown === 5 && lastAnnounced !== 'refreshed') {
            live.textContent = 'Metrics refreshed';
            lastAnnounced = 'refreshed';
        } else if (countdown !== 5) {
            lastAnnounced = null;
        }
    }, 1000);
}

function initialize() {
    console.log('Initializing Pipeline Control Center...');

    setupTabs();
    initializeCharts();
    startCountdown();

    addActivityLogItem('success', 'Pipeline Control Center initialized');

    fetchMetrics();
    setInterval(fetchMetrics, REFRESH_INTERVAL);

    console.log('Dashboard ready!');
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
} else {
    initialize();
}
