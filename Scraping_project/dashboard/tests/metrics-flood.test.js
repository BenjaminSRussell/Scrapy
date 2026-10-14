// Run: node --test Scraping_project/dashboard/tests/metrics-flood.test.js
// #690: a wide or slow metrics scrape hides chart history, keeps KPI counters,
// shows a banner, and restores charts after N calm intervals.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const {
    parseMetrics,
    metricSeriesCount,
    METRICS_FLOOD_DEFAULTS,
    metricsFloodLimits,
    advanceMetricsFlood,
} = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');

test('parseMetrics counts exposition series, not summed names (#690)', () => {
    const m = parseMetrics([
        '# HELP errors_total x',
        'errors_total{stage="s1"} 3',
        'errors_total{stage="s2"} 4',
        'pages_total 1',
        '',
        'not a series',
    ].join('\n'));
    assert.strictEqual(metricSeriesCount(m), 3);
    assert.strictEqual(m.errors_total, 7);
    assert.strictEqual(m['errors_total{stage="s1"}'], 3);
    assert.strictEqual(Object.keys(m).length, 4); // two labelled keys, the sum, pages_total
});

test('flood limits: defaults, window config, query override (#690)', () => {
    assert.deepStrictEqual(metricsFloodLimits(null), METRICS_FLOOD_DEFAULTS);
    assert.deepStrictEqual(METRICS_FLOOD_DEFAULTS, { maxSeries: 2000, maxParseMs: 50, recoverIntervals: 3 });
    const fromWindow = metricsFloodLimits({ CC_METRICS_FLOOD: { maxSeries: 100, maxParseMs: 10, recoverIntervals: 5 } });
    assert.deepStrictEqual(fromWindow, { maxSeries: 100, maxParseMs: 10, recoverIntervals: 5 });
    const mixed = metricsFloodLimits({
        CC_METRICS_FLOOD: { maxSeries: 100, maxParseMs: 10, recoverIntervals: 5 },
        search: '?tab=overview&floodMaxSeries=40&floodRecover=2',
    });
    assert.deepStrictEqual(mixed, { maxSeries: 40, maxParseMs: 10, recoverIntervals: 2 });
    assert.deepStrictEqual(
        metricsFloodLimits({ search: '?floodMaxSeries=no&floodMaxParseMs=-1&floodRecover=0' }),
        METRICS_FLOOD_DEFAULTS
    );
});

test('flood trips on series or parse time and recovers only after N calm intervals (#690)', () => {
    const limits = { maxSeries: 10, maxParseMs: 50, recoverIntervals: 3 };
    const calm = { seriesCount: 10, parseMs: 50 }; // equal to the limit is not a flood
    let state = advanceMetricsFlood({ degraded: false, calm: 0 }, calm, limits);
    assert.strictEqual(state.degraded, false);

    state = advanceMetricsFlood(state, { seriesCount: 11, parseMs: 1 }, limits);
    assert.strictEqual(state.degraded, true);
    assert.strictEqual(state.entered, true);
    assert.match(require('../format-utils.js').metricsFloodBannerText(state), /summary counters only/);
    assert.match(require('../format-utils.js').metricsFloodBannerText(state), /series count 11 exceeds 10/);

    state = advanceMetricsFlood({ degraded: false, calm: 0 }, { seriesCount: 1, parseMs: 50.1 }, limits);
    assert.strictEqual(state.overParse, true);
    assert.match(require('../format-utils.js').metricsFloodBannerText(state), /parse time 50\.1ms exceeds 50ms/);

    state = advanceMetricsFlood(state, calm, limits);
    assert.strictEqual(state.degraded, true);
    assert.strictEqual(state.calm, 1);
    state = advanceMetricsFlood(state, { seriesCount: 99, parseMs: 1 }, limits);
    assert.strictEqual(state.calm, 0, 'a hot sample resets the calm streak');
    assert.strictEqual(state.degraded, true);
    state = advanceMetricsFlood(state, calm, limits);
    state = advanceMetricsFlood(state, calm, limits);
    assert.strictEqual(state.degraded, true);
    assert.strictEqual(state.recovered, false);
    state = advanceMetricsFlood(state, calm, limits);
    assert.strictEqual(state.degraded, false);
    assert.strictEqual(state.recovered, true);
    assert.strictEqual(require('../format-utils.js').metricsFloodBannerText(state), '');
});

test('defaults are documented on the dashboard README (#690)', () => {
    assert.match(readme, /CC_METRICS_FLOOD\.maxSeries/);
    assert.match(readme, /floodMaxSeries=/);
    assert.match(readme, /\| `2000` \|/);
    assert.match(readme, /floodMaxParseMs=/);
    assert.match(readme, /\| `50` \|/);
    assert.match(readme, /floodRecover=/);
    assert.match(readme, /\| `3` \|/);
});

test('banner is in the page and chart history is hidden while flooded (#690)', () => {
    assert.match(html, /id="metrics-flood-banner"[^>]*role="status"[^>]*hidden/);
    assert.match(html, /id="metrics-flood-text"/);
    assert.match(html, /#main\.metrics-flood \.chart-container[\s\S]*display:\s*none/);
    assert.match(html, /#main\.metrics-flood \.overview-spark-wrap/);
});

function classList() {
    const set = new Set();
    return {
        add(c) { set.add(c); },
        remove(c) { set.delete(c); },
        contains(c) { return set.has(c); },
        toggle(c, force) {
            if (force === true) { set.add(c); return true; }
            if (force === false) { set.delete(c); return false; }
            if (set.has(c)) { set.delete(c); return false; }
            set.add(c);
            return true;
        },
    };
}

function el(id) {
    return { id, textContent: '', hidden: true, classList: classList(), attrs: {}, children: [], style: {}, offsetWidth: 1,
        setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute() {}, appendChild(c) { this.children.push(c); return c; },
        querySelector() { return null; }, querySelectorAll() { return []; } };
}

function loadApp() {
    const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    const main = el('main');
    const banner = el('metrics-flood-banner');
    const text = el('metrics-flood-text');
    const urls = el('topbar-urls');
    const known = { main, 'metrics-flood-banner': banner, 'metrics-flood-text': text, 'topbar-urls': urls };
    let chartUpdates = 0;
    const document = {
        readyState: 'loading',
        title: 'Pipeline Control Center',
        addEventListener() {},
        body: el('body'),
        getElementById(id) { return known[id] || null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        createElement: (tag) => el(tag),
    };
    const ctx = {
        ...require('../format-utils.js'),
        document,
        window: { CC_METRICS_FLOOD: { maxSeries: 5, maxParseMs: 100000, recoverIntervals: 3 } },
        console: { log() {}, warn() {}, error() {} },
        performance: { now: () => 0 },
        fetch: async () => ({ ok: true, statusText: 'OK', text: async () => '' }),
        chartUpdates: () => chartUpdates,
    };
    ctx.Chart = function Chart() { this.update = () => { chartUpdates += 1; }; };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    const chart = { data: { labels: [], datasets: [{ data: [] }] }, update() { chartUpdates += 1; } };
    vm.runInContext('charts.stageProgression = __chart; charts.routing = __chart;', Object.assign(ctx, { __chart: chart }));
    return { ctx, main, banner, text, urls, chartUpdates: () => chartUpdates };
}

test('degraded mode shows the banner, keeps counters, and skips charts until N calm scrapes (#690)', async () => {
    const { ctx, main, banner, text, urls, chartUpdates } = loadApp();
    const wide = ['stage1_urls_discovered_total 42'];
    for (let i = 0; i < 8; i++) wide.push(`extra_series_${i} ${i}`);
    ctx.fetch = async () => ({ ok: true, statusText: 'OK', text: async () => wide.join('\n') });
    await vm.runInContext('fetchMetrics()', ctx);
    assert.strictEqual(banner.hidden, false);
    assert.match(text.textContent, /Showing summary counters only/);
    assert.match(text.textContent, /series count/);
    assert.ok(main.classList.contains('metrics-flood'));
    assert.strictEqual(urls.textContent, '42');
    assert.strictEqual(chartUpdates(), 0);
    assert.strictEqual(vm.runInContext('historicalData.urls.length', ctx), 0);

    ctx.fetch = async () => ({ ok: true, statusText: 'OK', text: async () => 'stage1_urls_discovered_total 7\n' });
    await vm.runInContext('fetchMetrics()', ctx);
    await vm.runInContext('fetchMetrics()', ctx);
    assert.strictEqual(banner.hidden, false, 'two calm intervals are not enough');
    assert.strictEqual(chartUpdates(), 0);
    await vm.runInContext('fetchMetrics()', ctx);
    assert.strictEqual(banner.hidden, true);
    assert.ok(!main.classList.contains('metrics-flood'));
    assert.ok(chartUpdates() > 0);
    assert.strictEqual(vm.runInContext('historicalData.urls.length', ctx), 1);
    assert.strictEqual(urls.textContent, '7');
});
