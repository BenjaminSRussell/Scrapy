// Run: node --test Scraping_project/dashboard/tests/
// (#1014) 50 points at 5s is readable. A shorter interval or a longer buffer
// must not plot every raw sample: line charts downsample (LTTB) while
// historicalData keeps a 200-sample raw buffer. The 50-point ring (#691)
// still caps the activity log and any store that does not set rawCap.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const utils = require('../format-utils.js');
const {
    downsampleIndices,
    HISTORY_MAX_POINTS,
    HISTORY_RAW_POINTS,
    enforceHistoryCap,
    pushHistorySample,
    boundHistoryBuffer,
} = utils;

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function fakeChart() {
    return {
        options: { plugins: { legend: { display: false }, tooltip: { enabled: false } } },
        data: { labels: [], datasets: [{ data: [], label: 'A' }, { data: [], label: 'B' }] },
        update() {},
    };
}

function sandbox() {
    const document = {
        readyState: 'loading',
        addEventListener() {},
        getElementById() { return null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
    const ctx = { document, window: {}, console: { log() {}, warn() {}, error() {} }, ...utils };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    const charts = { urls: fakeChart(), pages: fakeChart(), summaries: fakeChart(), throughput: fakeChart() };
    ctx.__charts = charts;
    vm.runInContext('charts = __charts;', ctx);
    return { ctx, charts };
}

test('HISTORY_RAW_POINTS is the chart buffer and the shared ring stays 50', () => {
    assert.strictEqual(HISTORY_MAX_POINTS, 50);
    assert.strictEqual(HISTORY_RAW_POINTS, 200);
    const ring = [];
    for (let i = 0; i < 80; i++) pushHistorySample(ring, i);
    assert.strictEqual(ring.length, HISTORY_MAX_POINTS);
    const raw = [];
    for (let i = 0; i < HISTORY_RAW_POINTS + 10; i++) pushHistorySample(raw, i, HISTORY_RAW_POINTS);
    assert.strictEqual(raw.length, HISTORY_RAW_POINTS);
    assert.strictEqual(raw[0], 10);
    const unlabeled = { urls: Array.from({ length: 80 }, (_, i) => i) };
    enforceHistoryCap(unlabeled, 'start');
    assert.strictEqual(unlabeled.urls.length, HISTORY_MAX_POINTS);
    const charts = {
        rawCap: HISTORY_RAW_POINTS,
        urls: Array.from({ length: 210 }, (_, i) => i),
    };
    enforceHistoryCap(charts, 'start');
    assert.strictEqual(charts.urls.length, HISTORY_RAW_POINTS);
    assert.strictEqual(charts.urls[0], 10);
    const log = [];
    for (let i = 0; i < 80; i++) {
        log.unshift(i);
        boundHistoryBuffer(log, 'end');
    }
    assert.strictEqual(log.length, HISTORY_MAX_POINTS);
});

test('downsampleIndices is a no-op at or under the visual budget', () => {
    assert.deepStrictEqual(downsampleIndices([1, 2, 3, 4], 50), [0, 1, 2, 3]);
    assert.deepStrictEqual(downsampleIndices([], 50), []);
    assert.deepStrictEqual(downsampleIndices([[], []], 50), []);
    assert.deepStrictEqual(downsampleIndices([1, 2, 3, 4], 4), [0, 1, 2, 3]);
});

test('downsampleIndices keeps endpoints, length, and a spike stride would miss (#1014)', () => {
    const n = HISTORY_RAW_POINTS;
    const values = Array.from({ length: n }, (_, i) => (i === 101 ? 1000 : 0));
    const idx = downsampleIndices([values], HISTORY_MAX_POINTS, values.map((_, i) => i * 5000));
    assert.strictEqual(idx.length, HISTORY_MAX_POINTS);
    assert.strictEqual(idx[0], 0);
    assert.strictEqual(idx[idx.length - 1], n - 1);
    assert.ok(idx.includes(101), 'peak sample is plotted');
    assert.ok(idx.every((v, i) => i === 0 || v > idx[i - 1]));
    // Stride of 4 from 0 never lands on 101; LTTB must.
    assert.ok(!Array.from({ length: HISTORY_MAX_POINTS }, (_, k) => k * 4).includes(101));
});

test('downsampleIndices keeps a spike that is only on the second series', () => {
    const n = HISTORY_RAW_POINTS;
    const flat = Array.from({ length: n }, () => 1);
    const peaked = flat.map((v, i) => (i === 77 ? 999 : v));
    const idx = downsampleIndices([flat, peaked], HISTORY_MAX_POINTS);
    assert.strictEqual(idx.length, HISTORY_MAX_POINTS);
    assert.ok(idx.includes(77));
});

test('downsampleIndices tolerates non-finite values and tiny budgets', () => {
    const idx = downsampleIndices([NaN, Infinity, 1, 2, null, 5], 3);
    assert.deepStrictEqual(idx, [0, idx[1], 5]);
    assert.ok(idx[1] > 0 && idx[1] < 5);
    assert.deepStrictEqual(downsampleIndices(Array.from({ length: 10 }, (_, i) => i), 2), [0, 9]);
    assert.deepStrictEqual(downsampleIndices(Array.from({ length: 10 }, (_, i) => i), 1), [9]);
});

test('200 raw samples stay in the buffer and line charts plot the shared ring (#1014)', () => {
    const { ctx, charts } = sandbox();
    vm.runInContext(`
        const n = historicalData.rawCap;
        historicalData.urls = Array.from({ length: n }, (_, i) => i === 101 ? 5000 : i);
        historicalData.pages = historicalData.urls.slice();
        historicalData.summaries = Array.from({ length: n }, () => 0);
        historicalData.timestamps = Array.from({ length: n }, (_, i) => 't' + i);
        historicalData.sampleTimes = Array.from({ length: n }, (_, i) => 1700000000000 + i * 5000);
        updatePerformanceCharts();
    `, ctx);
    assert.strictEqual(vm.runInContext('historicalData.urls.length', ctx), HISTORY_RAW_POINTS);
    assert.strictEqual(vm.runInContext('historicalData.sampleTimes.length', ctx), HISTORY_RAW_POINTS);
    assert.strictEqual(vm.runInContext('historicalData.rawCap', ctx), HISTORY_RAW_POINTS);
    const plotted = charts.urls.data.datasets[0].data;
    assert.strictEqual(plotted.length, HISTORY_MAX_POINTS);
    assert.strictEqual(charts.urls.data.labels.length, plotted.length);
    assert.strictEqual(charts.pages.data.datasets[0].data.length, HISTORY_MAX_POINTS);
    assert.strictEqual(charts.summaries.data.datasets[0].data.length, HISTORY_MAX_POINTS);
    assert.strictEqual(plotted[0], 0);
    assert.strictEqual(plotted[plotted.length - 1], HISTORY_RAW_POINTS - 1);
    assert.ok(plotted.includes(5000));
    assert.strictEqual(charts.urls.data.labels[0], 't0');
    assert.strictEqual(charts.urls.data.labels[HISTORY_MAX_POINTS - 1], 't' + (HISTORY_RAW_POINTS - 1));
    assert.ok(charts.throughput.data.datasets[0].data.length <= HISTORY_MAX_POINTS);
    assert.ok(charts.throughput.data.datasets[0].data.length > 1);
    assert.strictEqual(
        charts.throughput.data.datasets[0].data.length,
        charts.throughput.data.datasets[1].data.length
    );
    // Sparklines still read the raw buffer (not the plotted subset).
    assert.strictEqual(vm.runInContext('historicalData.urls[101]', ctx), 5000);
    assert.ok(!/maxDataPoints/.test(appSrc));
});

test('updateHistoricalData retains 200 samples and drops the oldest after that', () => {
    const { ctx, charts } = sandbox();
    for (let i = 0; i < HISTORY_RAW_POINTS; i++) {
        vm.runInContext(
            `updateHistoricalData({ stage1_urls_discovered_total: ${i}, stage2_pages_analyzed_total: ${i}, stage3_summaries_created_total: ${i} })`,
            ctx
        );
    }
    assert.strictEqual(vm.runInContext('historicalData.urls.length', ctx), HISTORY_RAW_POINTS);
    assert.strictEqual(vm.runInContext('historicalData.timestamps.length', ctx), HISTORY_RAW_POINTS);
    assert.strictEqual(vm.runInContext('historicalData.sampleTimes.length', ctx), HISTORY_RAW_POINTS);
    assert.strictEqual(charts.urls.data.datasets[0].data.length, HISTORY_MAX_POINTS);
    const before = vm.runInContext('historicalData.urls[0]', ctx);
    vm.runInContext(
        'updateHistoricalData({ stage1_urls_discovered_total: 999, stage2_pages_analyzed_total: 999, stage3_summaries_created_total: 999 })',
        ctx
    );
    assert.strictEqual(vm.runInContext('historicalData.urls.length', ctx), HISTORY_RAW_POINTS);
    assert.notStrictEqual(vm.runInContext('historicalData.urls[0]', ctx), before);
    assert.strictEqual(vm.runInContext('historicalData.urls[199]', ctx), 999);
    assert.strictEqual(charts.urls.data.datasets[0].data.length, HISTORY_MAX_POINTS);
});

test('resetChartHistory clears the raw sample clock and keeps the raw cap', () => {
    const { ctx, charts } = sandbox();
    vm.runInContext(
        'updateHistoricalData({ stage1_urls_discovered_total: 1, stage2_pages_analyzed_total: 1, stage3_summaries_created_total: 1 })',
        ctx
    );
    assert.ok(vm.runInContext('historicalData.sampleTimes.length', ctx) > 0);
    vm.runInContext('resetChartHistory()', ctx);
    assert.strictEqual(vm.runInContext('historicalData.sampleTimes.length', ctx), 0);
    assert.strictEqual(vm.runInContext('historicalData.urls.length', ctx), 0);
    assert.strictEqual(vm.runInContext('historicalData.rawCap', ctx), HISTORY_RAW_POINTS);
    assert.strictEqual(charts.urls.data.labels.length, 0);
    assert.strictEqual(charts.urls.data.datasets[0].data.length, 0);
});
