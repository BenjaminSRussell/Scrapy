// Pipeline Control Center - Main Application
// Real-time monitoring dashboard for UConn scraping pipeline

// #1020: ?metrics= > localStorage > window.CC_METRICS_URL > relative /api/metrics.
// Filled in by loadMetricsChoice() once the storage helpers exist.
const STORE_METRICS_URL_KEY = 'cc.metricsUrl';
let METRICS_ENDPOINT = { url: '/api/metrics', source: 'default' };
let METRICS_URL = METRICS_ENDPOINT.url;
const REFRESH_INTERVAL = 5000;
const METRICS_TIMEOUT_MS = 4000; // #1032
const STORE_TAB_KEY = 'cc.lastTab'; // #1034
const STORE_INTERVAL_KEY = 'cc.refreshInterval'; // #1015
const STORE_DANGER_SOUND_KEY = 'cc.dangerSound'; // #1037
let dangerSoundEnabled = false;
let dangerAudioCtx = null;
const STORE_COMPARE_KEY = 'cc.comparePrevHour'; // #1035
const ACTIVITY_STORE_KEY = 'cc.activityLog'; // #1029 sessionStorage ring
const STORE_ACCENT_KEY = 'cc.accent'; // #1017
let refreshPaused = false;
let metricsFetcher = null;
let metricsWasDown = false;
let consecutiveFetchFailures = 0;
// #1030: last HEALTH_HISTORY_LIMIT metrics-poll outcomes (true = success).
let pollHistory = [];
// Static <title> captured once; connection state is prefixed onto it (#945).
const BASE_TITLE = (typeof document !== 'undefined' && document.title) || 'Pipeline Control Center';
let chartsHaveSample = false;
// #690: charts stay paused while a scrape is over the series or parse-time limit.
let metricsFlood = { degraded: false, calm: 0 };

let refreshScheduler = null;
let charts = {};
let comparisonEnabled = false; // #1035
let latestMetrics = {};
let historicalData = {
    timestamps: [],
    urls: [],
    pages: [],
    summaries: [],
    sampleTimes: [],  // ms, parallel to the arrays above (#141)
    // Plotted points stay HISTORY_MAX_POINTS. rawCap is the in-memory buffer
    // for rates and sparklines (#1014). The activity log stays on the shared
    // 50-point ring and does not follow a chart axis (#691).
    rawCap: HISTORY_RAW_POINTS,
};
let previousMetrics = {};
let previousMetricsAt = 0;  // ms timestamp of previousMetrics (#141)
let startTime = Date.now();
let activityLog = [];
let activityStaleText = null; // #1029 note for entries restored after a reload
// #1027: empty set shows every level. Pressed chips are the only levels on the Activity tab.
let activityLevelFilter = new Set();
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


// Chart.js comes from a CDN. When it is blocked (offline / corporate proxy)
// `Chart` is undefined; constructing charts used to throw inside initialize()
// and abort everything after it, including the metrics poll (#983). Charts are
// optional: degrade to a visible notice and keep the numeric metrics live.
let chartsUnavailable = false;

function showChartsUnavailable() {
    chartsUnavailable = true;
    document.querySelectorAll('.chart-container').forEach(el => {
        el.classList.add('is-unavailable');
        if (!el.querySelector('.chart-unavailable-note')) {
            const note = document.createElement('div');
            note.className = 'chart-unavailable-note';
            note.textContent = 'Chart unavailable';
            el.appendChild(note);
        }
    });
    if (document.getElementById('charts-unavailable')) return;
    const banner = document.createElement('div');
    banner.id = 'charts-unavailable';
    banner.className = 'charts-unavailable-banner';
    banner.setAttribute('role', 'status');
    banner.textContent = 'Charts unavailable: Chart.js could not be loaded (offline or CDN blocked). Numeric metrics still update.';
    const host = document.getElementById('main') || document.querySelector('.container') || document.body;
    host.insertBefore(banner, host.firstChild);
}

/** Returns true when charts were created, false when running chart-less. */
function initializeCharts() {
    if (typeof Chart === 'undefined') {
        console.warn('Chart.js not loaded; running without charts');
        showChartsUnavailable();
        return false;
    }
    try {
        buildCharts();
        return true;
    } catch (err) {
        console.error('Chart initialization failed:', err);
        Object.values(charts).forEach(ch => { try { ch.destroy(); } catch (_) {} });
        charts = {};
        showChartsUnavailable();
        return false;
    }
}

function buildCharts() {
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
    boundHistoryBuffer(activityLog, 'end');
    if (activityLog.length > ACTIVITY_LOG_CAP) {
        activityLog.length = ACTIVITY_LOG_CAP;
    }

    persistActivityLog();
    updateActivityLog();
    announceActivity(type, message);
}

// #962: the visual feeds are re-rendered wholesale (and live in hidden tabs),
// so they are aria-live="off"; only the new item goes to a dedicated,
// always-present live region. Text only, never HTML.
const ACTIVITY_ANNOUNCE_KEEP = 5;
const activityAnnouncer = createActivityAnnouncer();
function mediaQueryMatches(query) {
    try {
        return !!(window.matchMedia && window.matchMedia(query).matches);
    } catch (_) {
        return false;
    }
}

// Short sine blip. No asset file. A missing or suspended context stays silent.
function playDangerChime() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try {
        if (!dangerAudioCtx) dangerAudioCtx = new AC();
    } catch (_) {
        return;
    }
    const ctx = dangerAudioCtx;
    const start = () => {
        try {
            const t = ctx.currentTime;
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(880, t);
            osc.frequency.exponentialRampToValueAtTime(440, t + 0.12);
            gain.gain.setValueAtTime(0.0001, t);
            gain.gain.exponentialRampToValueAtTime(0.05, t + 0.015);
            gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(t);
            osc.stop(t + 0.18);
        } catch (_) { /* autoplay policy or a closed context */ }
    };
    if (ctx.state === 'suspended') {
        ctx.resume().then(start).catch(() => {});
    } else {
        start();
    }
}

function considerDangerSound(type, announced) {
    const decision = dangerSoundDecision({
        type,
        enabled: dangerSoundEnabled,
        reducedMotion: mediaQueryMatches('(prefers-reduced-motion: reduce)'),
        prefersMuted: mediaQueryMatches('(prefers-muted: reduce)') || mediaQueryMatches('(prefers-muted)'),
        announced,
    });
    if (decision.play) playDangerChime();
}

function announceActivity(type, message) {
    const region = document.getElementById('activity-announce');
    if (!region) return;
    const text = activityAnnouncer.text(type, message);
    considerDangerSound(type, text != null);
    if (!text) return;
    const node = document.createElement('div');
    node.textContent = text;
    region.appendChild(node);
    while (region.childElementCount > ACTIVITY_ANNOUNCE_KEEP) region.firstElementChild.remove();
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


// #906: rows are built with DOM APIs only (textContent / setAttribute), never
// innerHTML, so API- or metrics-derived text can't become markup.
function renderActivityMessage(el, message) {
    for (const seg of splitLinks(message)) {
        if (seg.href) {
            const a = document.createElement('a');
            a.href = seg.href;
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            a.textContent = seg.text;
            el.appendChild(a);
        } else {
            el.appendChild(document.createTextNode(seg.text));
        }
    }
}

function renderActivityList(container, { maxItems = HISTORY_MAX_POINTS, levels = null, query = '' } = {}) {
    if (!container) return 0;
    // null levels = unfiltered (overview). A selection, including an empty one, is the Activity tab.
    const source = levels == null ? activityLog : filterActivityItems(activityLog, levels);
    const capped = source.slice(0, maxItems);
    // #1028: case-insensitive message substring. Overview passes query ''.
    const items = filterActivityMessages(capped, query);
    const frag = document.createDocumentFragment();
    for (const item of items) {
        const li = document.createElement('li');
        li.className = 'activity-item';
        const type = String(item.type || '');
        if (/^[a-z-]+$/.test(type)) li.classList.add(type);
        const ts = document.createElement('div');
        ts.className = 'activity-timestamp';
        ts.textContent = String(item.timestamp ?? '');
        if (item.restored) {
            li.classList.add('restored');
            ts.appendChild(document.createTextNode(' \u00b7 '));
            const tag = document.createElement('span');
            tag.className = 'activity-restored-tag';
            tag.textContent = 'before reload';
            ts.appendChild(tag);
        }
        const msg = document.createElement('div');
        msg.className = 'activity-message';
        renderActivityMessage(msg, item.message);
        li.append(ts, msg);
        frag.appendChild(li);
    }
    container.replaceChildren(frag);
    return items.length;
}

function currentActivityQuery() {
    const input = document.getElementById('activity-search');
    return input ? input.value : '';
}

function syncActivitySearchStatus() {
    const capped = filterActivityItems(activityLog, activityLevelFilter).slice(0, HISTORY_MAX_POINTS);
    const query = currentActivityQuery();
    const shown = filterActivityMessages(capped, query).length;
    const status = document.getElementById('activity-filter-status');
    const clearBtn = document.getElementById('clear-activity-search');
    const text = activityFilterStatus(shown, capped.length, query);
    if (clearBtn) clearBtn.disabled = text === '';
    if (status) {
        status.textContent = text;
        status.hidden = text === '';
    }
}

// Search and Clear bypass the scroll pin so the visible rows match the field.
function renderActivitySearch() {
    const log = document.getElementById('activity-log');
    if (log) {
        renderActivityList(log, {
            maxItems: HISTORY_MAX_POINTS,
            levels: activityLevelFilter,
            query: currentActivityQuery()
        });
    }
    syncActivityLevelChips();
    syncActivitySearchStatus();
}

function setupActivitySearch() {
    const input = document.getElementById('activity-search');
    const clearBtn = document.getElementById('clear-activity-search');
    if (input) {
        const apply = () => renderActivitySearch();
        input.addEventListener('input', apply);
        input.addEventListener('search', apply);
        input.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape' || !input.value) return;
            event.preventDefault();
            input.value = '';
            apply();
        });
    }
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            if (input) {
                input.value = '';
                input.focus();
            }
            renderActivitySearch();
        });
    }
}

function updateActivityLog() {

    const logContainers = [
        document.getElementById('overview-activity'),
        document.getElementById('activity-log')
    ];

    logContainers.forEach(container => {
        if (!container) return;
        if (activityPinned && container.id === "activity-log" && currentActivityQuery().trim() === '') return;
        const maxItems = container.id === 'overview-activity' ? 8 : HISTORY_MAX_POINTS;
        // #1027: chips filter the Activity tab only. Overview stays the latest mix.
        const levels = container.id === 'activity-log' ? activityLevelFilter : null;
        // #1028: search filters the Activity tab only. Overview stays unfiltered.
        const query = container.id === 'activity-log' ? currentActivityQuery() : '';
        renderActivityList(container, { maxItems, levels, query });
    });
    syncActivityLevelChips();
    syncActivitySearchStatus();
}

function syncActivityLevelChips() {
    const counts = activityLevelCounts(activityLog);
    document.querySelectorAll('#tab-activity .activity-chip[data-level]').forEach(btn => {
        const level = btn.getAttribute('data-level');
        const countEl = btn.querySelector('[data-level-count]');
        if (countEl) countEl.textContent = String(counts[level] || 0);
        btn.setAttribute('aria-pressed', activityLevelFilter.has(level) ? 'true' : 'false');
    });
    const empty = document.getElementById('activity-filter-empty');
    if (!empty) return;
    const filtering = activityLevelFilter.size > 0;
    const matches = filtering ? filterActivityItems(activityLog, activityLevelFilter).length : activityLog.length;
    empty.hidden = !filtering || matches > 0;
}

// #1029: sessionStorage can be missing or throw (privacy mode, quota). Never fatal.
function sessionGet(key) {
    try {
        if (typeof window === 'undefined' || !window.sessionStorage) return null;
        return window.sessionStorage.getItem(key);
    } catch (_) { return null; }
}
function sessionSet(key, value) {
    try {
        if (typeof window !== 'undefined' && window.sessionStorage) window.sessionStorage.setItem(key, String(value));
    } catch (_) { /* quota / privacy */ }
}
function sessionRemove(key) {
    try {
        if (typeof window !== 'undefined' && window.sessionStorage) window.sessionStorage.removeItem(key);
    } catch (_) { /* ignore */ }
}

function persistActivityLog() {
    const items = capActivityLog(activityLog);
    if (!items.length) {
        sessionRemove(ACTIVITY_STORE_KEY);
        return;
    }
    sessionSet(ACTIVITY_STORE_KEY, JSON.stringify(items));
}

function syncActivityStaleNotes() {
    ['overview-activity', 'activity-log'].forEach((id) => {
        const feed = document.getElementById(id);
        if (!feed || !feed.parentNode) return;
        const noteId = 'activity-stale-note-' + id;
        let note = document.getElementById(noteId);
        if (!activityStaleText) {
            if (note) note.remove();
            return;
        }
        if (!note) {
            note = document.createElement('p');
            note.id = noteId;
            note.className = 'activity-stale-note';
            note.setAttribute('role', 'status');
            note.textContent = activityStaleText;
            feed.parentNode.insertBefore(note, feed);
            return;
        }
        if (note.textContent !== activityStaleText) note.textContent = activityStaleText;
    });
}

function restorePersistedActivityLog() {
    const restored = restoreActivityLog(sessionGet(ACTIVITY_STORE_KEY));
    activityLog = restored.items.map((item) => ({
        type: item.type,
        message: item.message,
        timestamp: item.timestamp,
        restored: true
    }));
    activityStaleText = restored.staleNote;
    syncActivityStaleNotes();
    if (activityLog.length) updateActivityLog();
}

function clearActivityLog() {
    activityLog.length = 0;
    activityStaleText = null;
    activityPinned = false;
    persistActivityLog();
    syncActivityStaleNotes();
    updateActivityLog();
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
    latestMetrics = metrics || {};
    const now = new Date();
    const timeLabel = now.toLocaleTimeString();

    // Each series is capped on its own at rawCap. A short chart-label series
    // must not leave urls, pages, summaries, or sampleTimes free to grow (#691).
    // Line charts downsample that buffer to HISTORY_MAX_POINTS (#1014).
    const cap = historicalData.rawCap;
    pushHistorySample(historicalData.timestamps, timeLabel, cap);
    pushHistorySample(historicalData.urls, metrics['stage1_urls_discovered_total'] || 0, cap);
    pushHistorySample(historicalData.pages, metrics['stage2_pages_analyzed_total'] || 0, cap);
    pushHistorySample(historicalData.summaries, metrics['stage3_summaries_created_total'] || 0, cap);
    pushHistorySample(historicalData.sampleTimes, now.getTime(), cap);
    enforceHistoryCap(historicalData, 'start');

    updatePerformanceCharts();
    // Leave the cold-chart state (#1076) on the first real sample. The helper
    // existed but was never called, so legends/tooltips stayed off and the
    // "Waiting for first metrics sample" placeholder never cleared (#977).
    markChartsHaveSample();
}

function projectAt(values, indices) {
    const out = new Array(indices.length);
    for (let k = 0; k < indices.length; k++) out[k] = values[indices[k]];
    return out;
}

function updatePerformanceCharts() {
    // Plot a downsampled view. historicalData itself stays the raw buffer (#1014).
    const plotMax = HISTORY_MAX_POINTS;
    const idx = downsampleIndices(
        [historicalData.urls, historicalData.pages, historicalData.summaries],
        plotMax,
        historicalData.sampleTimes
    );
    const labels = projectAt(historicalData.timestamps, idx);

    if (charts.urls) {
        charts.urls.data.labels = labels;
        charts.urls.data.datasets[0].data = projectAt(historicalData.urls, idx);
        charts.urls.update('none');
    }

    if (charts.pages) {
        charts.pages.data.labels = labels;
        charts.pages.data.datasets[0].data = projectAt(historicalData.pages, idx);
        charts.pages.update('none');
    }

    if (charts.summaries) {
        charts.summaries.data.labels = labels;
        charts.summaries.data.datasets[0].data = projectAt(historicalData.summaries, idx);
        charts.summaries.update('none');
    }

    if (charts.throughput && historicalData.timestamps.length > 1) {
        const urlsRate = [];
        const pagesRate = [];
        const rateTimes = [];
        const rateLabels = [];

        for (let i = 1; i < historicalData.urls.length; i++) {
            const ms = historicalData.sampleTimes[i] - historicalData.sampleTimes[i-1];
            urlsRate.push(ratePerMinute(historicalData.urls[i], historicalData.urls[i-1], ms));
            pagesRate.push(ratePerMinute(historicalData.pages[i], historicalData.pages[i-1], ms));
            rateTimes.push(historicalData.sampleTimes[i]);
            rateLabels.push(historicalData.timestamps[i]);
        }

        const rateIdx = downsampleIndices([urlsRate, pagesRate], plotMax, rateTimes);
        charts.throughput.data.labels = projectAt(rateLabels, rateIdx);
        charts.throughput.data.datasets[0].data = projectAt(urlsRate, rateIdx);
        charts.throughput.data.datasets[1].data = projectAt(pagesRate, rateIdx);
        charts.throughput.update('none');
    }
    applyComparisonBaselines();
    syncAllChartTables();
    updateOverviewSparklines();
}

// Line charts that can carry a previous-hour overlay (#1035). Bars and the
// routing doughnut are snapshots, not time series, so they are left alone.
const COMPARISON_CHARTS = {
    urls: ['urls'],
    pages: ['pages'],
    summaries: ['summaries'],
    throughput: ['urlsRate', 'pagesRate'],
};

function livePointCount(chart, live, index) {
    const ds = live[index];
    if (ds && Array.isArray(ds.data)) return ds.data.length;
    if (index === 0 && chart.data && Array.isArray(chart.data.labels)) return chart.data.labels.length;
    return 0;
}

function applyComparisonBaselines() {
    let placeholderCount = 0;
    let realCount = 0;
    for (const [chartKey, seriesKeys] of Object.entries(COMPARISON_CHARTS)) {
        const chart = charts[chartKey];
        if (!chart || !chart.data || !chart.data.datasets) continue;
        const live = chart.data.datasets.filter(ds => ds.ccBaseline !== COMPARISON_BASELINE_ROLE);
        if (!comparisonEnabled) {
            if (live.length !== chart.data.datasets.length) {
                chart.data.datasets = live;
                try { chart.update('none'); } catch (_) {}
            }
            continue;
        }
        const next = live.slice();
        seriesKeys.forEach((seriesKey, i) => {
            const series = comparisonBaselineSeries(
                livePointCount(chart, live, i),
                readPrevHourAverage(latestMetrics, seriesKey)
            );
            if (series.placeholder) placeholderCount += 1;
            else realCount += 1;
            next.push(series);
        });
        chart.data.datasets = next;
        try { chart.update('none'); } catch (_) {}
    }
    const note = document.getElementById('comparison-baseline-note');
    if (!note) return;
    const text = comparisonNote(comparisonEnabled, placeholderCount, realCount);
    if (!text) {
        note.textContent = '';
        note.hidden = true;
        return;
    }
    if (note.hidden) note.hidden = false;
    if (note.textContent !== text) note.textContent = text;
}

function setComparisonMode(on, persist) {
    comparisonEnabled = !!on;
    const btn = document.getElementById('compare-prev-hour');
    if (btn) btn.setAttribute('aria-pressed', comparisonEnabled ? 'true' : 'false');
    if (persist) storeSet(STORE_COMPARE_KEY, comparisonEnabled ? '1' : '0');
    applyComparisonBaselines();
    if (typeof syncAllChartTables === 'function') syncAllChartTables();
}

function setupComparisonToggle() {
    const btn = document.getElementById('compare-prev-hour');
    if (!btn) return;
    btn.addEventListener('click', () => setComparisonMode(!comparisonEnabled, true));
    if (storeGet(STORE_COMPARE_KEY) === '1') setComparisonMode(true, false);
}

// Highlight the Pipeline-tab stage cards that processed work in the last
// interval; previously stages 1-2 were hardcoded active forever (#976).
function applyStageActivity(rates) {
    const flags = stageActivity(rates);
    document.querySelectorAll('#tab-pipeline .stage-card').forEach((card, i) => {
        const on = !!flags[i];
        card.classList.toggle('active', on);
        card.dataset.activity = on ? 'active' : 'idle';
    });
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
        // spoken once via the activity announcer (#962), not a second region
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

    // topbar status is owned by setConnectionStatus (#945)
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

    applyStageActivity(rates);

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

    const lastUpdateEl = document.getElementById('last-update');
    if (lastUpdateEl) lastUpdateEl.textContent = formatEpochTime(metrics['pipeline_last_update_timestamp']);

    // #389/#393: these tiles used to be hardcoded (12 tables, 8 scouts). Show
    // a real value when the exporter reports one, otherwise an explicit dash.
    setReportedValue('delta-table-count', countLabelValues(metrics, 'delta_lake_records', 'table'));
    setReportedValue('scout-instances',
        'pipeline_scout_instances' in metrics ? metrics['pipeline_scout_instances'] : null);
    const lastRefreshEl = document.getElementById('last-refresh-time');
    if (lastRefreshEl) lastRefreshEl.textContent = new Date().toLocaleTimeString();
    const uptimeEl = document.getElementById('uptime');
    if (uptimeEl) uptimeEl.textContent = getUptime();

    // #690: summary counters above stay live. Chart history does not.
    if (!metricsFlood.degraded) {
        if (charts.stageProgression) {
            charts.stageProgression.data.datasets[0].data = [s1Discovered, s2Analyzed, s3Summaries, s4Summaries];
            charts.stageProgression.update('none');
        }

        if (charts.routing) {
            charts.routing.data.datasets[0].data = [s2Quality, s2Massive];
            charts.routing.update('none');
            syncAllChartTables();
        }
    }

    if (Object.keys(previousMetrics).length > 0) {
        detectSignificantChanges(metrics);
    }

    if (!metricsFlood.degraded) updateHistoricalData(metrics);
    renderScrapeTargets(metrics);

    if (metricsFlood.degraded) {
        previousMetrics = {
            stage1_urls_discovered_total: metrics['stage1_urls_discovered_total'],
            stage2_pages_analyzed_total: metrics['stage2_pages_analyzed_total'],
            stage3_summaries_created_total: metrics['stage3_summaries_created_total'],
            stage4_large_doc_summaries_total: metrics['stage4_large_doc_summaries_total'],
            stage3_documents_deduplicated_total: metrics['stage3_documents_deduplicated_total'],
        };
    } else {
        previousMetrics = { ...metrics };
    }
    previousMetricsAt = Date.now();
    syncStageDrilldownValues();
}

// #1052: rank hosts from the metrics sample (or an API stub of the same
// shape). Rows are DOM nodes with textContent so a host cannot become markup.
function renderScrapeTargets(metrics) {
    const empty = document.getElementById('scrape-targets-empty');
    const table = document.getElementById('scrape-targets-table');
    const list = document.getElementById('scrape-targets-list');
    if (!empty || !table || !list || typeof topScrapeTargets !== 'function') return;
    const summary = topScrapeTargets(metrics);
    list.replaceChildren();
    if (summary.missing) {
        empty.hidden = false;
        empty.textContent = summary.empty;
        table.hidden = true;
        return;
    }
    empty.hidden = true;
    table.hidden = false;
    for (const row of summary.rows) {
        const tr = document.createElement('tr');
        const host = document.createElement('td');
        host.textContent = row.host;
        const value = document.createElement('td');
        value.textContent = Number.isFinite(row.value) ? formatNumber(row.value) : '\u2014';
        tr.appendChild(host);
        tr.appendChild(value);
        list.appendChild(tr);
    }
}

function readMetricsFloodLimits() {
    const loc = typeof location !== 'undefined' ? location : null;
    const win = typeof window !== 'undefined' ? window : {};
    return metricsFloodLimits({
        CC_METRICS_FLOOD: win.CC_METRICS_FLOOD,
        search: loc ? loc.search : '',
    });
}

function clockMs() {
    return (typeof performance !== 'undefined' && typeof performance.now === 'function')
        ? performance.now()
        : Date.now();
}

function applyMetricsFlood(decision) {
    const was = !!metricsFlood.degraded;
    metricsFlood = { degraded: !!decision.degraded, calm: decision.calm || 0 };
    const banner = document.getElementById('metrics-flood-banner');
    const text = document.getElementById('metrics-flood-text');
    const host = document.getElementById('main');
    if (host && host.classList) host.classList.toggle('metrics-flood', !!decision.degraded);
    if (banner) {
        if (decision.degraded) {
            if (text) text.textContent = metricsFloodBannerText(decision);
            banner.hidden = false;
        } else {
            banner.hidden = true;
        }
    }
    if (decision.entered && !was) {
        addActivityLogItem('warning', 'Metrics flood: charts paused, showing summary counters only');
    } else if (decision.recovered) {
        addActivityLogItem('success', 'Metrics flood cleared: charts resumed');
    }
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

function renderHealthHistory() {
    const strip = document.getElementById('health-history');
    if (!strip) return;
    const view = healthHistoryView(pollHistory, HEALTH_HISTORY_LIMIT);
    strip.replaceChildren();
    strip.setAttribute('aria-label', view.label);
    for (const kind of view.slots) {
        const seg = document.createElement('span');
        seg.className = 'health-seg health-seg-' + kind;
        seg.setAttribute('aria-hidden', 'true');
        strip.appendChild(seg);
    }
    const caption = document.getElementById('health-history-caption');
    if (caption) caption.textContent = view.caption;
}

function notePollOutcome(ok) {
    pollHistory = pushPollOutcome(pollHistory, ok, HEALTH_HISTORY_LIMIT);
    renderHealthHistory();
}

function setConnectionStatus(kind, pipelineRunning) {
    // kind: 'online' | 'never' | 'offline'
    const state = connectionState(kind, pipelineRunning, consecutiveFetchFailures);
    const sys = document.getElementById('system-status');
    const top = document.getElementById('topbar-status');
    const labels = {
        online: 'Online',
        never: 'Not connected',
        offline: 'Disconnected',
    };
    if (sys) {
        sys.classList.remove('online', 'offline', 'never');
        sys.classList.add(kind === 'online' ? 'online' : kind === 'never' ? 'never' : 'offline');
        const span = sys.querySelector('span:last-child');
        if (span) { const __n = labels[kind] || kind; if (span.textContent !== String(__n)) { span.textContent = __n; span.classList.remove('flash'); void span.offsetWidth; span.classList.add('flash'); } else { span.textContent = __n; } }
    }
    const title = documentTitle(state, BASE_TITLE);
    if (document.title !== title) document.title = title;
    if (top) { const __n = state.top; if (top.textContent !== String(__n)) { top.textContent = __n; top.classList.remove('flash'); void top.offsetWidth; top.classList.add('flash'); } else { top.textContent = __n; } }
}

// #401: Redis queue depths from serve.py. Only when served over http(s) (the
// API lives on this origin); a file:// page has no server to ask.
const QUEUES_URL = '/api/queues';
async function fetchQueueDepths() {
    if (typeof window === 'undefined' || !/^https?:$/.test(window.location.protocol)) return;
    const status = document.getElementById('queue-depths-status');
    const tbody = document.getElementById('queue-depths');
    if (!status || !tbody) return;
    let payload = null;
    try {
        const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
        const timer = ctrl ? setTimeout(() => ctrl.abort(), METRICS_TIMEOUT_MS) : null;
        try {
            const resp = await fetch(QUEUES_URL, { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined });
            payload = await resp.json().catch(() => ({ ok: false, error: `HTTP ${resp.status}` }));
        } finally {
            if (timer) clearTimeout(timer);
        }
    } catch (error) {
        payload = { ok: false, error: (error && error.name === 'AbortError') ? 'timed out' : 'request failed' };
    }
    const { error, rows } = queueDepthRows(payload);
    tbody.replaceChildren();
    if (error) {
        status.textContent = error;
        return;
    }
    status.textContent = rows.length ? `Updated ${new Date().toLocaleTimeString()}` : 'No queue keys configured (CC_QUEUE_KEYS).';
    for (const row of rows) {
        const tr = document.createElement('tr');
        for (const value of [row.key, row.type, row.depth === null ? '—' : formatNumber(row.depth)]) {
            const td = document.createElement('td');
            td.textContent = String(value);
            tr.appendChild(td);
        }
        tbody.appendChild(tr);
    }
}

async function fetchMetrics() {
    const main = document.getElementById('main') || document.querySelector('.container');
    if (main) main.setAttribute('aria-busy', 'true');
    try {
        // #1031/#1032: aborts a previous in-flight request; times out after 4s.
        if (!metricsFetcher) {
            metricsFetcher = createMetricsFetcher({ fetch: (u, init) => fetch(u, init), timeoutMs: METRICS_TIMEOUT_MS });
        }
        const text = await metricsFetcher.fetchText(METRICS_URL);
        const parseStarted = clockMs();
        const metrics = parseMetrics(text);
        const decision = advanceMetricsFlood(metricsFlood, {
            seriesCount: metricSeriesCount(metrics),
            parseMs: clockMs() - parseStarted,
        }, readMetricsFloodLimits());
        applyMetricsFlood(decision);
        updateDashboard(metrics);
        hasEverSucceeded = true;
        lastMetricsAt = Date.now();
        consecutiveFetchFailures = 0;
        finishLoading(false);
        hideFetchError();
        renderStale();
        setConnectionStatus('online', metrics['pipeline_running']);
        notePollOutcome(true);
    } catch (error) {
        if (error && error.superseded) return; // a newer request owns the result (#1031)
        metricsWasDown = true;
        console.error('Error fetching metrics:', error);
        const failure = metricsFailureActivity(error, METRICS_TIMEOUT_MS);
        addActivityLogItem(failure.type, failure.message);
        if (!hasEverSucceeded) finishLoading(true);
        showFetchError(failure.message);
        consecutiveFetchFailures += 1;
        setConnectionStatus(hasEverSucceeded ? 'offline' : 'never');
        notePollOutcome(false);
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
    for (const key of Object.keys(historicalData)) {
        if (Array.isArray(historicalData[key])) historicalData[key].length = 0;
    }
    lastHistoryDayKey = null;
    Object.values(charts || {}).forEach(ch => {
        if (!ch?.data) return;
        ch.data.labels = [];
        (ch.data.datasets || []).forEach(ds => { ds.data = []; });
        try { ch.update('none'); } catch (_) {}
    });
    applyComparisonBaselines();
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
    document.querySelectorAll('.chart-container .skeleton').forEach(el => el.remove());
}

// #1025: one placeholder block. Cards use it as a bar; charts fill the plot.
function skeletonEl() {
    const el = document.createElement('div');
    el.className = 'skeleton';
    el.setAttribute('aria-hidden', 'true');
    return el;
}

function ensureChartPlaceholders() {
    if (chartsHaveSample || chartsUnavailable) return;
    document.querySelectorAll('.chart-container').forEach(el => {
        el.classList.add('is-empty');
        if (!el.querySelector('.skeleton')) {
            el.insertBefore(skeletonEl(), el.firstChild);
        }
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
        btn.setAttribute('tabindex', on ? '0' : '-1'); // #153 roving tabindex
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
    storeSet(STORE_TAB_KEY, tabName); // #1034
    if (pushUrl) {
        const url = new URL(window.location.href);
        url.searchParams.set('tab', tabName);
        url.hash = tabName;
        history.replaceState(null, '', url);
    }
    return true;
}

// localStorage can be missing or throw (privacy mode, file://); never fatal.
function storeGet(key) {
    try { return window.localStorage ? window.localStorage.getItem(key) : null; } catch (_) { return null; }
}
function storeSet(key, value) {
    try { if (window.localStorage) window.localStorage.setItem(key, String(value)); } catch (_) { /* ignore */ }
}
function storeDel(key) {
    try { if (window.localStorage) window.localStorage.removeItem(key); } catch (_) { /* ignore */ }
}
function storeRemove(key) {
    try { if (window.localStorage) window.localStorage.removeItem(key); } catch (_) { /* ignore */ }
}

// #1017: accent picker writes --accent / --accent-ink / --accent-border.
function applyAccent(hex) {
    const vars = accentCssVars(hex);
    const root = document.documentElement;
    Object.keys(vars).forEach((name) => root.style.setProperty(name, vars[name]));
    const input = document.getElementById('accent-color');
    if (input && input.value !== vars['--accent']) input.value = vars['--accent'];
    return vars;
}

function setupAccentControl() {
    const stored = storeGet(STORE_ACCENT_KEY);
    const color = parseAccentColor(stored);
    if (stored && color !== String(stored).trim().toLowerCase()) storeDel(STORE_ACCENT_KEY);
    applyAccent(color);
    const input = document.getElementById('accent-color');
    if (input) {
        input.addEventListener('input', () => {
            const next = parseAccentColor(input.value);
            applyAccent(next);
            storeSet(STORE_ACCENT_KEY, next);
            const live = document.getElementById('refresh-status');
            if (live) live.textContent = 'Accent color updated';
        });
    }
    const resetBtn = document.getElementById('accent-reset');
    if (resetBtn) {
        resetBtn.addEventListener('click', () => {
            storeDel(STORE_ACCENT_KEY);
            applyAccent(DEFAULT_ACCENT);
            const live = document.getElementById('refresh-status');
            if (live) live.textContent = 'Accent color reset';
        });
    }
}

// #1020: query wins, then a remembered http(s) URL, then CC_METRICS_URL, then
// the relative default. A valid ?metrics= is what later visits remember.
function loadMetricsChoice() {
    const loc = typeof location !== 'undefined' ? location : null;
    METRICS_ENDPOINT = metricsEndpoint(loc, {
        stored: storeGet(STORE_METRICS_URL_KEY),
        override: (typeof window !== 'undefined' && window.CC_METRICS_URL) || null,
    });
    METRICS_URL = METRICS_ENDPOINT.url;
    if (METRICS_ENDPOINT.source === 'query') storeSet(STORE_METRICS_URL_KEY, METRICS_URL);
}
loadMetricsChoice();

function renderConnectionPopover() {
    const urlEl = document.getElementById('connection-metrics-url');
    const srcEl = document.getElementById('connection-metrics-source');
    const loc = typeof location !== 'undefined' ? location : null;
    if (urlEl) urlEl.textContent = METRICS_URL;
    if (srcEl) srcEl.textContent = metricsEndpointLabel(METRICS_ENDPOINT, loc);
    const forget = document.getElementById('connection-forget');
    if (!forget || forget.dataset.bound === '1') return;
    forget.dataset.bound = '1';
    const remembered = METRICS_ENDPOINT.source === 'query' || METRICS_ENDPOINT.source === 'storage';
    forget.hidden = !remembered;
    forget.addEventListener('click', () => {
        storeRemove(STORE_METRICS_URL_KEY);
        if (!loc) return;
        const next = new URL(loc.href);
        next.searchParams.delete('metrics');
        loc.assign(next.toString());
    });
}

function visibleTabNames() {
    return Array.from(document.querySelectorAll('.tab-button'))
        .filter(b => !b.hidden && b.style.display !== 'none')
        .map(b => b.getAttribute('data-tab'));
}

// #952/#1010: 1-5 switch tabs, R refreshes (see shortcutAction in format-utils.js).
function setupShortcuts() {
    visibleTabNames().forEach((name, i) => {
        const btn = document.querySelector(`.tab-button[data-tab="${name}"]`);
        if (btn && i < 9) btn.setAttribute('aria-keyshortcuts', String(i + 1));
    });
    const manualBtn = document.getElementById('manual-refresh');
    if (manualBtn) manualBtn.setAttribute('aria-keyshortcuts', 'R');
    const hint = document.getElementById('shortcut-hint');
    if (hint) hint.textContent = shortcutHintText(visibleTabNames().length);  // #503
    document.addEventListener('keydown', (event) => {
        // Stage drill-down owns the keyboard while it is open (#1033).
        if (stageDrilldownIsOpen()) return;
        const action = shortcutAction(event, visibleTabNames());
        if (!action) return;
        event.preventDefault();
        if (action.type === 'tab') {
            activateTab(action.tab, true);
            const btn = document.querySelector(`.tab-button[data-tab="${action.tab}"]`);
            if (btn) btn.focus();
        } else if (action.type === 'refresh' && refreshScheduler) {
            requestRefresh();
        }
    });
}

// #963: no polling while the tab is hidden; fetch at once when it is shown
// again. A manual Pause (refreshPaused) is kept either way.
function setupVisibilityPause() {
    document.addEventListener('visibilitychange', () => {
        if (!refreshScheduler) return;
        const live = document.getElementById('refresh-status');
        if (document.hidden) {
            refreshScheduler.setPaused(true);
        } else if (!refreshPaused) {
            refreshScheduler.setPaused(false);
            refreshScheduler.refreshNow();
            if (live) live.textContent = 'Auto-refresh resumed';
        }
        renderCountdown();
    });
}

// #1037: muted until the operator opts in. The choice survives reloads.
function setupDangerSound() {
    dangerSoundEnabled = parseDangerSoundEnabled(storeGet(STORE_DANGER_SOUND_KEY));
    const box = document.getElementById('danger-sound-toggle');
    if (!box) return;
    box.checked = dangerSoundEnabled;
    box.addEventListener('change', () => {
        dangerSoundEnabled = !!box.checked;
        storeSet(STORE_DANGER_SOUND_KEY, dangerSoundEnabled ? '1' : '0');
        if (dangerSoundEnabled) {
            const AC = window.AudioContext || window.webkitAudioContext;
            try {
                if (AC && !dangerAudioCtx) dangerAudioCtx = new AC();
                if (dangerAudioCtx && dangerAudioCtx.state === 'suspended') {
                    dangerAudioCtx.resume().catch(() => {});
                }
            } catch (_) { /* toggle still persists without Web Audio */ }
        }
        const live = document.getElementById('refresh-status');
        if (live) {
            live.textContent = dangerSoundEnabled
                ? 'Sound alerts on for new danger activity'
                : 'Sound alerts off';
        }
    });
}

// #1015: 5/10/30s interval picker, remembered in localStorage.
function setupIntervalControl() {
    const select = document.getElementById('refresh-interval');
    const ms = parseRefreshInterval(storeGet(STORE_INTERVAL_KEY), REFRESH_INTERVAL);
    refreshScheduler.setInterval(ms);
    if (!select) return;
    select.value = String(ms);
    select.addEventListener('change', () => {
        const v = parseRefreshInterval(select.value, REFRESH_INTERVAL);
        refreshScheduler.setInterval(v);
        storeSet(STORE_INTERVAL_KEY, v);
        renderCountdown();
        const live = document.getElementById('refresh-status');
        if (live) live.textContent = `Auto-refresh every ${v / 1000} seconds`;
    });
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
    if (clearBtn) clearBtn.addEventListener('click', clearActivityLog);
    const exportJson = document.getElementById('export-activity-json');
    if (exportJson) exportJson.addEventListener('click', () => exportActivityLog('json'));
    const exportCsv = document.getElementById('export-activity-csv');
    if (exportCsv) exportCsv.addEventListener('click', () => exportActivityLog('csv'));

    // #1027: toggle chips. No chip pressed shows every level; Danger alone is danger-only.
    const filterGroup = document.querySelector('#tab-activity .activity-filters');
    if (filterGroup) {
        filterGroup.addEventListener('click', (event) => {
            const btn = event.target.closest('button[data-level]');
            if (!btn || !filterGroup.contains(btn)) return;
            activityLevelFilter = toggleActivityLevel(activityLevelFilter, btn.getAttribute('data-level'));
            activityPinned = false;
            updateActivityLog();
        });
    }

    const tabButtons = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabName = button.getAttribute('data-tab');
            activateTab(tabName, true);
        });
    });

    // #153: WAI-ARIA tabs pattern. Arrow keys / Home / End move between the
    // visible tabs and select them (automatic activation); Tab moves into the panel.
    const tablist = document.querySelector('[role="tablist"]');
    if (tablist) {
        tablist.addEventListener('keydown', (event) => {
            const tabs = Array.from(tablist.querySelectorAll('[role="tab"]')).filter(t => !t.hidden && t.style.display !== 'none');
            const current = tabs.indexOf(document.activeElement);
            if (current < 0) return;
            const next = tabKeyTarget(event.key, current, tabs.length);
            if (next === null) return;
            event.preventDefault();
            const target = tabs[next];
            activateTab(target.getAttribute('data-tab'), true);
            target.focus();
        });
    }
}

// #352: until the first successful fetch, numbers are placeholders, not zeros.
const LOADING_SELECTOR = '.metric-value, .stage-metric-value, .topbar-stat-value, .health-value';
function markLoading() {
    document.querySelectorAll(LOADING_SELECTOR).forEach(el => {
        el.classList.add('is-loading');
        el.setAttribute('aria-busy', 'true');
    });
    const top = document.getElementById('topbar-status');
    if (top) top.textContent = 'Loading…';
}
function finishLoading(failed) {
    document.querySelectorAll('.is-loading').forEach(el => {
        el.classList.remove('is-loading');
        el.removeAttribute('aria-busy');
        // Never fetched: say "unknown" instead of revealing the static 0 / Never.
        if (failed) el.textContent = '\u2014';
    });
}

// #362: visible error with a Retry control; cleared by the next success.
function showFetchError(message) {
    const banner = document.getElementById('fetch-error-banner');
    const text = document.getElementById('fetch-error-text');
    // Unhide before writing so role=alert announces the new text.
    if (banner && banner.hidden) banner.hidden = false;
    if (text) text.textContent = `Could not load metrics: ${message}`;
}
function hideFetchError() {
    const banner = document.getElementById('fetch-error-banner');
    if (banner && !banner.hidden) banner.hidden = true;
}

// #351: Stale badge once the last good sample is older than 3 refresh intervals.
let staleShown = false;
function renderStale() {
    const interval = refreshScheduler ? refreshScheduler.getInterval() : REFRESH_INTERVAL;
    const { stale } = staleState(lastMetricsAt, Date.now(), interval, 3);
    if (stale === staleShown) return;
    staleShown = stale;
    ['stale-badge', 'topbar-stale'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.hidden = !stale;
    });
    const live = document.getElementById('refresh-status');
    if (live) live.textContent = stale ? 'Metrics are stale: no successful update for several intervals' : 'Metrics are current again';
}

// #349: Refresh now shows a busy state while a fetch is in flight (it stays
// focusable: aria-disabled, not disabled), and the countdown restarts after it.
function setRefreshBusy(busy) {
    const btn = document.getElementById('manual-refresh');
    if (!btn) return;
    btn.setAttribute('aria-busy', busy ? 'true' : 'false');
    btn.setAttribute('aria-disabled', busy ? 'true' : 'false');
    btn.textContent = busy ? 'Refreshing…' : 'Refresh now';
}
function requestRefresh() {
    if (!refreshScheduler || refreshScheduler.isFetching()) return;
    refreshScheduler.refreshNow();
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
    renderStale();
}

function startCountdown() {
    renderCountdown();
    setInterval(renderCountdown, 250);
}

// One aria announcement per completed successful fetch (#1093).
async function fetchAndAnnounce() {
    const before = lastMetricsAt;
    setRefreshBusy(true);
    try {
        await Promise.all([fetchMetrics(), fetchQueueDepths()]);
    } finally {
        setRefreshBusy(false);
    }
    const live = document.getElementById('refresh-status');
    if (live && lastMetricsAt !== before) live.textContent = 'Metrics refreshed';
    renderCountdown();
}


// #1033: click or Enter/Space on a pipeline stage card opens a side panel
// with a subset of that stage's metrics and placeholder job/queue links.
let stageDrilldownReturnFocus = null;

function stageDrilldownIsOpen() {
    const root = document.getElementById('stage-drilldown');
    return !!(root && !root.hidden);
}

function renderStageDrilldown(stage) {
    const spec = stageDrilldown(stage);
    const root = document.getElementById('stage-drilldown');
    if (!spec || !root) return false;
    const title = document.getElementById('stage-drilldown-title');
    if (title) title.textContent = `Stage ${spec.stage}: ${spec.title}`;
    const card = document.querySelector(`#tab-pipeline .stage-card[data-stage="${spec.stage}"]`);
    const status = document.getElementById('stage-drilldown-status');
    if (status) status.textContent = card && card.dataset.activity === 'active' ? 'Active' : 'Idle';
    const list = document.getElementById('stage-drilldown-metrics');
    if (list) {
        list.replaceChildren();
        spec.metrics.forEach(metric => {
            const src = document.getElementById(metric.id);
            const row = document.createElement('div');
            row.className = 'stage-metric';
            const label = document.createElement('span');
            label.className = 'stage-metric-label';
            label.textContent = metric.label;
            const value = document.createElement('span');
            value.className = 'stage-metric-value';
            value.dataset.metricId = metric.id;
            value.textContent = src ? src.textContent : '\u2014';
            row.append(label, value);
            list.append(row);
        });
    }
    const links = document.getElementById('stage-drilldown-links');
    if (links) {
        links.replaceChildren();
        spec.links.forEach(link => {
            const li = document.createElement('li');
            const a = document.createElement('a');
            a.href = link.href;
            a.className = 'stage-drilldown-link';
            a.dataset.placeholder = 'true';
            a.setAttribute('aria-disabled', 'true');
            a.textContent = `${link.label} (coming soon)`;
            li.append(a);
            links.append(li);
        });
    }
    root.dataset.stage = String(spec.stage);
    document.querySelectorAll('#tab-pipeline .stage-card').forEach(el => {
        el.setAttribute('aria-expanded', el === card ? 'true' : 'false');
    });
    return true;
}

function syncStageDrilldownValues() {
    if (!stageDrilldownIsOpen()) return;
    const root = document.getElementById('stage-drilldown');
    const spec = stageDrilldown(root.dataset.stage);
    if (!spec) return;
    const card = document.querySelector(`#tab-pipeline .stage-card[data-stage="${spec.stage}"]`);
    const status = document.getElementById('stage-drilldown-status');
    if (status) {
        const next = card && card.dataset.activity === 'active' ? 'Active' : 'Idle';
        if (status.textContent !== next) status.textContent = next;
    }
    root.querySelectorAll('[data-metric-id]').forEach(el => {
        const src = document.getElementById(el.dataset.metricId);
        if (src && el.textContent !== src.textContent) el.textContent = src.textContent;
    });
}

function openStageDrilldown(stage, trigger) {
    if (!renderStageDrilldown(stage)) return;
    const root = document.getElementById('stage-drilldown');
    root.hidden = false;
    stageDrilldownReturnFocus = trigger || document.activeElement;
    const closeBtn = document.getElementById('stage-drilldown-close');
    if (closeBtn) closeBtn.focus();
    const live = document.getElementById('stage-drilldown-announce');
    const title = document.getElementById('stage-drilldown-title');
    if (live && title) live.textContent = `Opened ${title.textContent}`;
}

function closeStageDrilldown() {
    const root = document.getElementById('stage-drilldown');
    if (!root || root.hidden) return;
    root.hidden = true;
    document.querySelectorAll('#tab-pipeline .stage-card').forEach(el => {
        el.setAttribute('aria-expanded', 'false');
    });
    const live = document.getElementById('stage-drilldown-announce');
    if (live) live.textContent = 'Closed stage details';
    const back = stageDrilldownReturnFocus;
    stageDrilldownReturnFocus = null;
    if (back && typeof back.focus === 'function') back.focus();
}

function stageDrilldownFocusables() {
    const root = document.getElementById('stage-drilldown');
    if (!root) return [];
    return Array.from(root.querySelectorAll('.stage-drilldown-panel button, .stage-drilldown-panel a[href]'));
}

function setupStageDrilldown() {
    const root = document.getElementById('stage-drilldown');
    if (!root || typeof stageDrilldown !== 'function') return;
    document.querySelectorAll('#tab-pipeline .stage-card').forEach(card => {
        card.addEventListener('click', () => openStageDrilldown(card.dataset.stage, card));
        card.addEventListener('keydown', (event) => {
            if (stageDrilldownKey(event.key) !== 'activate') return;
            event.preventDefault();
            openStageDrilldown(card.dataset.stage, card);
        });
    });
    const closeBtn = document.getElementById('stage-drilldown-close');
    if (closeBtn) closeBtn.addEventListener('click', closeStageDrilldown);
    const backdrop = document.getElementById('stage-drilldown-backdrop');
    if (backdrop) backdrop.addEventListener('click', closeStageDrilldown);
    root.addEventListener('click', (event) => {
        const link = event.target.closest && event.target.closest('a[data-placeholder="true"]');
        if (link) event.preventDefault();
    });
    document.addEventListener('keydown', (event) => {
        if (!stageDrilldownIsOpen()) return;
        if (stageDrilldownKey(event.key) === 'close') {
            event.preventDefault();
            closeStageDrilldown();
            return;
        }
        if (event.key !== 'Tab') return;
        const items = stageDrilldownFocusables();
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        const active = document.activeElement;
        if (event.shiftKey && (active === first || !root.contains(active))) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && (active === last || !root.contains(active))) {
            event.preventDefault();
            first.focus();
        }
    });
}

function prefersReducedMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

// #1022: draw one check on Start after a confirmed job start. Reduced motion
// shows that check with no stroke animation. This does not launch a crawl
// and never adds confetti.
function showJobStarted(root) {
    const scope = root && root.querySelector ? root : document;
    const mark = (scope.querySelector && scope.querySelector('.job-started-check'))
        || document.querySelector('#job-start .job-started-check');
    if (!mark) return null;
    const view = jobStartedPresentation(prefersReducedMotion());
    mark.hidden = false;
    mark.classList.remove('is-on', 'is-static');
    if (view.animate) {
        void mark.getBoundingClientRect();
        mark.classList.add('is-on');
    } else {
        mark.classList.add('is-static');
    }
    const live = document.getElementById('job-start-status');
    if (live) live.textContent = view.label;
    return view;
}

function setupJobStarted() {
    document.addEventListener('cc:job-started', (ev) => {
        showJobStarted(ev && ev.target);
    });
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

/** System Health card: Grafana home from CC_GRAFANA_URL (#1040). */
function applyGrafanaLink() {
    const link = document.getElementById('grafana-analytics-link');
    if (!link) return;
    const home = grafanaHomeUrl(window.__CC_GRAFANA_URL__, link.getAttribute('href') || undefined);
    link.href = home;
    const shown = document.getElementById('grafana-analytics-base');
    if (shown) shown.textContent = home;
}

/** #404: first-run checklist. Built with textContent only; service details are untrusted text. */
function renderFirstRunChecks(checks) {
    const list = document.getElementById('first-run-checks');
    if (!list) return;
    list.replaceChildren(...checks.map(check => {
        const item = document.createElement('li');
        item.className = check.ok ? 'first-run-check ok' : 'first-run-check down';
        const name = document.createElement('strong');
        name.textContent = check.name;
        const state = document.createElement('span');
        state.textContent = check.ok ? ' healthy' : ' not reachable';
        const detail = document.createElement('small');
        detail.textContent = ` (${check.detail || 'no detail'})`;
        item.append(name, state, detail);
        return item;
    }));
}

function setFirstRunStatus(text) {
    const status = document.getElementById('first-run-status');
    if (status) status.textContent = text;
}

async function refreshFirstRunChecks() {
    try {
        const res = await fetch('/api/first-run', { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const state = await res.json();
        renderFirstRunChecks(state.checks || []);
        return state;
    } catch (err) {
        setFirstRunStatus(`Could not check services: ${err.message}`);
        return null;
    }
}

async function postFirstRun(path) {
    const res = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
    });
    return { status: res.status, body: await res.json().catch(() => ({})) };
}

/** #404: wizard shows once; completion is recorded server-side under data/config/. */
async function setupFirstRunWizard() {
    const wizard = document.getElementById('first-run-wizard');
    if (!wizard) return;
    const state = await refreshFirstRunChecks();
    if (!state || state.completed) return;
    wizard.hidden = false;
    const grafana = document.getElementById('first-run-grafana');
    if (grafana) grafana.href = grafanaHomeUrl(window.__CC_GRAFANA_URL__);

    document.getElementById('first-run-recheck').addEventListener('click', () => {
        setFirstRunStatus('Checking services...');
        refreshFirstRunChecks().then(() => setFirstRunStatus(''));
    });
    document.getElementById('first-run-load-seeds').addEventListener('click', async () => {
        setFirstRunStatus('Loading sample seeds...');
        try {
            const { body } = await postFirstRun('/api/first-run/sample-seeds');
            setFirstRunStatus(body.failed && body.failed.length
                ? `Loaded ${body.added || 0} sample seeds; ${body.failed.length} failed.`
                : `Loaded ${body.added || 0} sample seeds.`);
        } catch (err) {
            setFirstRunStatus(`Sample seed load failed: ${err.message}`);
        }
    });
    document.getElementById('first-run-finish').addEventListener('click', async () => {
        try {
            const { status } = await postFirstRun('/api/first-run/complete');
            if (status !== 200) throw new Error(`HTTP ${status}`);
            wizard.hidden = true;
            addActivityLogItem('success', 'First-run setup complete');
        } catch (err) {
            setFirstRunStatus(`Could not save setup state: ${err.message}`);
        }
    });
}

function initialize() {
    console.log('Initializing Pipeline Control Center...');

    applyFeatureFlags();
    setupAccentControl();
    applyVersionWatermark();
    // #1038: Settings panel. A missing helper must not block the metrics poll.
    if (typeof renderMetricNameMap === 'function') {
        renderMetricNameMap(document.getElementById('metric-name-map-body'), document);
    }
    renderHealthHistory();
    applyGrafanaLink();
    setupFirstRunWizard();
    renderConnectionPopover();
    setupTabs();
    setupActivitySearch();
    setupStageDrilldown();
    setupShortcuts();
    const initialTab = pickInitialTab(tabFromLocation(), storeGet(STORE_TAB_KEY), visibleTabNames());
    if (initialTab) activateTab(initialTab, false);
    markLoading();
    initializeCharts();
    ensureChartPlaceholders();
    setupComparisonToggle();
    refreshScheduler = createRefreshScheduler({ interval: REFRESH_INTERVAL, fetch: fetchAndAnnounce });
    setupIntervalControl();
    setupVisibilityPause();
    setupDangerSound();
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
    if (manualBtn) manualBtn.addEventListener('click', requestRefresh);
    const retryBtn = document.getElementById('retry-fetch');
    if (retryBtn) retryBtn.addEventListener('click', requestRefresh);
    setupJobStarted();

    restorePersistedActivityLog(); // #1029: before the fresh init line, so it stays newest
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

    // #910: a rejected ?metrics= override is a config error, not a silent fallback.
    const metricsProblem = metricsUrlProblem(typeof location !== 'undefined' ? location : null);
    if (metricsProblem) {
        console.error(`[config] ${metricsProblem}`);
        addActivityLogItem('danger', metricsProblem);
    }

    console.log('Dashboard ready!');
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
} else {
    initialize();
}
