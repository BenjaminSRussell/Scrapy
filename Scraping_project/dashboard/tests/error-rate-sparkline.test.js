// Run: node --test Scraping_project/dashboard/tests/
// Overview error/sec card (#1003): em dash when pipeline_errors_total is
// absent, sparkline path changes on later polls when the counter is present.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const utils = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('errorPerSecond is null when the counter is missing or not yet differenced', () => {
    const { errorPerSecond } = utils;
    assert.strictEqual(errorPerSecond(undefined, 10, 5000), null);
    assert.strictEqual(errorPerSecond(null, 10, 5000), null);
    assert.strictEqual(errorPerSecond(NaN, 10, 5000), null);
    assert.strictEqual(errorPerSecond(10, undefined, 5000), null);
    assert.strictEqual(errorPerSecond(10, 0, 0), null);
    assert.strictEqual(errorPerSecond(10, 10, 5000), 0);
    assert.strictEqual(errorPerSecond(4, 10, 5000), 0); // reset
    assert.strictEqual(errorPerSecond(20, 10, 5000), 2);
});

test('formatErrorRate and badge: dash, ok under 0.1/s, high above', () => {
    const { formatErrorRate, errorRateBadge } = utils;
    assert.strictEqual(formatErrorRate(null), '\u2014');
    assert.strictEqual(formatErrorRate(2), '2.00/s');
    assert.strictEqual(formatErrorRate(12.34), '12.3/s');
    assert.strictEqual(formatErrorRate(120), '120/s');
    assert.strictEqual(errorRateBadge(null).text, '\u2014');
    assert.strictEqual(errorRateBadge(0.1).level, 'success');
    assert.strictEqual(errorRateBadge(0.11).level, 'danger');
});

test('labelled pipeline_errors_total sums before the per-second rate (#365)', () => {
    const first = utils.parseMetrics([
        'pipeline_errors_total{stage="s1",site_id="uconn"} 4',
        'pipeline_errors_total{stage="s2",site_id="uconn"} 6',
    ].join('\n'));
    const second = utils.parseMetrics([
        'pipeline_errors_total{stage="s1",site_id="uconn"} 9',
        'pipeline_errors_total{stage="s2",site_id="uconn"} 11',
    ].join('\n'));
    assert.strictEqual(first.pipeline_errors_total, 10);
    assert.strictEqual(second.pipeline_errors_total, 20);
    assert.strictEqual(utils.errorPerSecond(second.pipeline_errors_total, first.pipeline_errors_total, 5000), 2);
});

test('Overview card shows an em dash before any sample', () => {
    const overview = html.slice(html.indexOf('id="tab-overview"'), html.indexOf('id="tab-pipeline"'));
    assert.match(overview, /id="overview-error-card"/);
    assert.match(overview, /id="overview-error-rate">(?:—|&mdash;)</);
    assert.match(overview, /id="overview-error-badge">(?:—|&mdash;)</);
    assert.match(overview, /id="spark-overview-error-rate"[^>]*aria-label="Error rate trend, errors per second"/);
});

function metricEl(text) {
    return {
        textContent: text,
        className: '',
        innerHTML: '',
        classList: { add() {}, remove() {} },
        get offsetWidth() { return 1; },
    };
}

function sandbox() {
    const value = metricEl('\u2014');
    const badge = metricEl('\u2014');
    badge.className = 'card-badge badge-info';
    const svg = metricEl('');
    const dir = metricEl('Trend: waiting for samples');
    const els = {
        'overview-error-rate': value,
        'overview-error-badge': badge,
        'spark-overview-error-rate': svg,
        'spark-dir-overview-error-rate': dir,
    };
    const document = {
        readyState: 'loading',
        addEventListener() {},
        getElementById(id) { return els[id] || null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
    const ctx = { ...utils, document, window: {}, console: { log() {}, warn() {}, error() {} } };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    return { ctx, value, badge, svg, dir };
}

function poll(ctx, sample, t) {
    ctx.__metrics = sample;
    vm.runInContext(`updateHistoricalData(__metrics, new Date(${t}))`, ctx);
    vm.runInContext('previousMetrics = Object.assign({}, __metrics); previousMetricsAt = ' + t + ';', ctx);
}

test('card stays an em dash when the metric is missing, including after labelled-only other series', () => {
    const { ctx, value, svg, dir } = sandbox();
    const other = utils.parseMetrics('stage1_urls_discovered_total 3\nscrapy_spider_errors_total{spider="s"} 9');
    poll(ctx, other, 1_000_000);
    assert.strictEqual(value.textContent, '\u2014');
    assert.strictEqual(svg.innerHTML, '');
    assert.strictEqual(dir.textContent, 'Trend: metric not reported');
    assert.strictEqual(vm.runInContext('historicalData.errorRates.length', ctx), 0);
});

test('sparkline path updates on each poll once two rates exist', () => {
    const { ctx, value, badge, svg, dir } = sandbox();
    poll(ctx, { pipeline_errors_total: 10 }, 1_000_000);
    assert.strictEqual(value.textContent, '\u2014'); // no previous sample yet
    assert.strictEqual(svg.innerHTML, '');

    poll(ctx, { pipeline_errors_total: 20 }, 1_005_000);
    assert.strictEqual(value.textContent, '2.00/s');
    assert.strictEqual(badge.textContent, 'High');
    assert.strictEqual(badge.className, 'card-badge badge-danger');
    assert.strictEqual(svg.innerHTML, ''); // one rate point is not a line
    assert.strictEqual(dir.textContent, 'Trend: waiting for samples');

    poll(ctx, { pipeline_errors_total: 30 }, 1_010_000);
    assert.strictEqual(value.textContent, '2.00/s');
    const firstPath = svg.innerHTML;
    assert.match(firstPath, /<path d="M/);
    assert.match(dir.textContent, /Trend: ● flat/);

    poll(ctx, { pipeline_errors_total: 50 }, 1_015_000);
    assert.strictEqual(value.textContent, '4.00/s');
    assert.notStrictEqual(svg.innerHTML, firstPath);
    assert.match(dir.textContent, /Trend: ▲ rising/);

    poll(ctx, { pipeline_errors_total: 50 }, 1_020_000);
    assert.strictEqual(value.textContent, '0.00/s');
    assert.strictEqual(badge.textContent, 'OK');
    assert.strictEqual(badge.className, 'card-badge badge-success');

    const rates = JSON.parse(vm.runInContext('JSON.stringify(historicalData.errorRates)', ctx));
    poll(ctx, { stage1_urls_discovered_total: 1 }, 1_025_000);
    assert.strictEqual(value.textContent, '\u2014');
    assert.strictEqual(dir.textContent, 'Trend: metric not reported');
    assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(historicalData.errorRates)', ctx)), rates);
});
