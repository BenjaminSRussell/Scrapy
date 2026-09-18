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

function updateActivityLog() {
    const logContainers = [
        document.getElementById('overview-activity'),
        document.getElementById('activity-log')
    ];

    logContainers.forEach(container => {
        if (!container) return;

        container.innerHTML = activityLog.map(item => `
            <div class="activity-item ${item.type}">
                <div class="activity-timestamp">${item.timestamp}</div>
                <div class="activity-message">${item.message}</div>
            </div>
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
    document.getElementById('topbar-urls').textContent = formatNumber(s1Discovered);
    document.getElementById('topbar-summaries').textContent = formatNumber(s3Summaries);

    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s1-discovered`);
        if (elem) elem.textContent = formatNumber(s1Discovered);
    });
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s1-queued`);
        if (elem) elem.textContent = formatNumber(s1Queued);
    });
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s2-analyzed`);
        if (elem) elem.textContent = formatNumber(s2Analyzed);
    });
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s2-quality`);
        if (elem) elem.textContent = formatNumber(s2Quality);
    });

    const s2MassiveElem = document.getElementById('pipeline-s2-massive');
    if (s2MassiveElem) s2MassiveElem.textContent = formatNumber(s2Massive);

    const s2WordsElem = document.getElementById('pipeline-s2-words');
    if (s2WordsElem) s2WordsElem.textContent = formatNumber(s2Words);

    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s3-summaries`);
        if (elem) elem.textContent = formatNumber(s3Summaries);
    });
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s3-dedup`);
        if (elem) elem.textContent = formatNumber(s3Dedup);
    });
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s4-summaries`);
        if (elem) elem.textContent = formatNumber(s4Summaries);
    });

    const compressionRatio = s4Compression > 0 ? (1 / s4Compression).toFixed(0) + 'x' : '0x';
    ['overview', 'pipeline'].forEach(prefix => {
        const elem = document.getElementById(`${prefix}-s4-compression`);
        if (elem) elem.textContent = compressionRatio;
    });

    const s3RateElem = document.getElementById('pipeline-s3-rate');
    if (s3RateElem) s3RateElem.textContent = rates.summaries.toFixed(1) + '/s';

    const s4RateElem = document.getElementById('pipeline-s4-rate');
    if (s4RateElem) s4RateElem.textContent = rates.largeDocs.toFixed(1) + '/s';

    document.getElementById('perf-s1-rate').textContent = rates.urls.toFixed(1) + ' URLs/min';
    document.getElementById('perf-s2-rate').textContent = rates.pages.toFixed(2) + ' pages/sec';
    document.getElementById('perf-s3-rate').textContent = rates.summaries.toFixed(2) + ' summaries/sec';
    document.getElementById('perf-s4-rate').textContent = rates.largeDocs.toFixed(2) + ' docs/sec';

    const redisKeys = metrics['pipeline_redis_keys'] || 0;
    const redisMemory = metrics['pipeline_redis_memory_bytes'] || 0;
    document.getElementById('redis-keys').textContent = formatNumber(redisKeys);
    document.getElementById('redis-memory').textContent = formatBytes(redisMemory);

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

async function fetchMetrics() {
    try {
        const response = await fetch(METRICS_URL);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const text = await response.text();
        const metrics = parseMetrics(text);
        updateDashboard(metrics);

        countdown = 5;
    } catch (error) {
        console.error('Error fetching metrics:', error);
        addActivityLogItem('danger', `Failed to fetch metrics: ${error.message}`);
    }
}

function setupTabs() {
    const tabButtons = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabName = button.getAttribute('data-tab');

            tabButtons.forEach(btn => btn.classList.remove('active'));
            tabContents.forEach(content => content.classList.remove('active'));

            button.classList.add('active');
            document.getElementById(`tab-${tabName}`).classList.add('active');
        });
    });
}

function startCountdown() {
    setInterval(() => {
        countdown--;
        if (countdown <= 0) {
            countdown = 5;
        }
        document.getElementById('refresh-countdown').textContent = countdown;
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
