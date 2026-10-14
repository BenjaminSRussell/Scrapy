// Run: node --test Scraping_project/dashboard/tests/
// #1035: comparison toggle overlays a dashed previous-hour baseline.
// Missing gauges stay a placeholder; finite gauges replace that series.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const utils = require('../format-utils.js');
const {
    parseMetrics, readPrevHourAverage, comparisonBaselineSeries, comparisonNote,
    COMPARISON_BASELINE_ROLE,
} = utils;

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('previous-hour gauges parse and missing values stay empty', () => {
    const m = parseMetrics([
        'stage1_urls_discovered_prev_hour_avg 12.5',
        'stage2_pages_per_min_prev_hour_avg 0',
        'stage3_summaries_created_prev_hour_avg nope',
    ].join('\n'));
    assert.strictEqual(readPrevHourAverage(m, 'urls'), 12.5);
    assert.strictEqual(readPrevHourAverage(m, 'pagesRate'), 0);
    assert.strictEqual(readPrevHourAverage(m, 'summaries'), null);
    assert.strictEqual(readPrevHourAverage({}, 'urls'), null);
    assert.strictEqual(readPrevHourAverage(m, 'nope'), null);
});

test('baseline series is dashed, and only a finite average leaves placeholder mode', () => {
    const stub = comparisonBaselineSeries(3, null);
    assert.strictEqual(stub.ccBaseline, COMPARISON_BASELINE_ROLE);
    assert.deepStrictEqual(stub.borderDash, [6, 4]);
    assert.strictEqual(stub.fill, false);
    assert.strictEqual(stub.placeholder, true);
    assert.deepStrictEqual(stub.data, [0, 0, 0]);
    assert.match(stub.label, /placeholder/i);

    const zero = comparisonBaselineSeries(2, 0);
    assert.strictEqual(zero.placeholder, false);
    assert.deepStrictEqual(zero.data, [0, 0]);
    assert.strictEqual(zero.label, 'Previous hour avg');

    assert.deepStrictEqual(comparisonBaselineSeries(0, 4).data, []);
    assert.deepStrictEqual(comparisonBaselineSeries(-2, 4).data, []);
    assert.strictEqual(comparisonBaselineSeries(1, 'nope').placeholder, true);
    assert.deepStrictEqual(comparisonBaselineSeries(2, 8).data, [8, 8]);
});

test('comparison note names the placeholder until every series has an average', () => {
    assert.strictEqual(comparisonNote(false, 2, 0), '');
    assert.match(comparisonNote(true, 2, 0), /placeholder baseline/);
    assert.match(comparisonNote(true, 0, 0), /Placeholder baseline is ready/);
    assert.match(comparisonNote(true, 0, 2), /previous hour average/);
    assert.doesNotMatch(comparisonNote(true, 0, 2), /placeholder baseline/);
    assert.match(comparisonNote(true, 1, 1), /placeholder baseline/);
});

test('the refresh bar exposes one comparison toggle', () => {
    assert.match(html, /id="compare-prev-hour"[^>]*aria-pressed="false"/);
    assert.match(html, /id="comparison-baseline-note"[^>]*role="status"[^>]*hidden/);
    assert.match(html, /aria-describedby="comparison-baseline-note"/);
});

function lineChart(datasets) {
    return {
        data: { labels: datasets[0].data.map((_, i) => String(i)), datasets },
        updates: 0,
        update() { this.updates += 1; },
    };
}

function sandbox() {
    const note = { hidden: true, textContent: '' };
    const btn = {
        attrs: {},
        setAttribute(k, v) { this.attrs[k] = v; },
        addEventListener() {},
    };
    const document = {
        readyState: 'loading',
        addEventListener() {},
        getElementById(id) {
            if (id === 'comparison-baseline-note') return note;
            if (id === 'compare-prev-hour') return btn;
            return null;
        },
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
    const ctx = {
        document,
        window: {},
        console: { log() {}, warn() {}, error() {} },
        ...utils,
    };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    return { ctx, note, btn };
}

test('toggle shows a dashed placeholder baseline and swaps in API averages', () => {
    const { ctx, note, btn } = sandbox();
    const urls = lineChart([{ label: 'URLs Discovered', data: [1, 2, 4] }]);
    const throughput = lineChart([
        { label: 'URLs/min', data: [1, 2] },
        { label: 'Pages/min', data: [3, 4] },
    ]);
    ctx.__urls = urls;
    ctx.__throughput = throughput;
    vm.runInContext('charts = { urls: __urls, throughput: __throughput }; setComparisonMode(true, false);', ctx);

    assert.strictEqual(btn.attrs['aria-pressed'], 'true');
    assert.strictEqual(note.hidden, false);
    assert.match(note.textContent, /placeholder baseline/);

    const urlsBase = urls.data.datasets.filter(ds => ds.ccBaseline === COMPARISON_BASELINE_ROLE);
    assert.strictEqual(urlsBase.length, 1);
    assert.strictEqual(urlsBase[0].placeholder, true);
    assert.deepStrictEqual(urlsBase[0].borderDash, [6, 4]);
    assert.deepStrictEqual(urlsBase[0].data, [0, 0, 0]);
    assert.strictEqual(urls.data.datasets[0].label, 'URLs Discovered');

    const rateBases = throughput.data.datasets.filter(ds => ds.ccBaseline === COMPARISON_BASELINE_ROLE);
    assert.strictEqual(rateBases.length, 2);
    assert.ok(rateBases.every(ds => ds.placeholder && ds.borderDash[0] === 6));

    vm.runInContext('latestMetrics = { stage1_urls_discovered_prev_hour_avg: 42, stage1_urls_per_min_prev_hour_avg: 3 }; applyComparisonBaselines();', ctx);
    const filled = urls.data.datasets.find(ds => ds.ccBaseline === COMPARISON_BASELINE_ROLE);
    assert.strictEqual(filled.placeholder, false);
    assert.deepStrictEqual(filled.data, [42, 42, 42]);
    assert.strictEqual(filled.label, 'Previous hour avg');
    const rates = throughput.data.datasets.filter(ds => ds.ccBaseline === COMPARISON_BASELINE_ROLE);
    assert.strictEqual(rates[0].placeholder, false);
    assert.deepStrictEqual(rates[0].data, [3, 3]);
    assert.strictEqual(rates[1].placeholder, true);
    assert.match(note.textContent, /placeholder baseline/);

    vm.runInContext('setComparisonMode(false, false);', ctx);
    assert.strictEqual(btn.attrs['aria-pressed'], 'false');
    assert.strictEqual(note.hidden, true);
    assert.ok(!urls.data.datasets.some(ds => ds.ccBaseline));
    assert.strictEqual(throughput.data.datasets.length, 2);
});

test('a later sample grows the placeholder to the live series length', () => {
    const { ctx } = sandbox();
    const urls = lineChart([{ label: 'URLs Discovered', data: [] }]);
    ctx.__urls = urls;
    vm.runInContext('charts = { urls: __urls }; setComparisonMode(true, false);', ctx);
    vm.runInContext('updateHistoricalData({ stage1_urls_discovered_total: 5, stage2_pages_analyzed_total: 1, stage3_summaries_created_total: 0 });', ctx);
    const base = urls.data.datasets.find(ds => ds.ccBaseline === COMPARISON_BASELINE_ROLE);
    assert.ok(base);
    assert.strictEqual(base.placeholder, true);
    assert.strictEqual(base.data.length, urls.data.datasets[0].data.length);
    // historicalData lives in the vm, so compare values, not array identity.
    assert.strictEqual(urls.data.datasets[0].data.length, 1);
    assert.strictEqual(Number(urls.data.datasets[0].data[0]), 5);
    assert.strictEqual(Number(base.data[0]), 0);
});
