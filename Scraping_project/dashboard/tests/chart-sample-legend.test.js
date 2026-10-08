// Run: node --test Scraping_project/dashboard/tests/
// (#977) The first real sample must leave the cold-chart state (#1076):
// markChartsHaveSample existed but was never called, so legends/tooltips stayed
// off and placeholders never cleared. The routing doughnut legend is laid out
// by width so it neither clips nor eats the ring on narrow cards.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const utils = require('../format-utils.js');
const { doughnutLegendLayout } = utils;

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function fakeChart() {
    return {
        options: { plugins: { legend: { display: false, position: 'bottom' }, tooltip: { enabled: false } } },
        data: { labels: [], datasets: [{ data: [] }, { data: [] }] },
        updates: 0,
        update() { this.updates++; },
    };
}

function sandbox() {
    const removed = [];
    const document = {
        readyState: 'loading',
        addEventListener() {},
        getElementById() { return null; },
        querySelector() { return null; },
        querySelectorAll(sel) {
            if (sel === '.chart-empty-placeholder') return [{ remove() { removed.push('ph'); } }];
            if (sel === '.chart-container.is-empty') return [{ classList: { remove() { removed.push('empty'); } } }];
            return [];
        },
    };
    const ctx = { document, window: {}, console: { log() {}, warn() {}, error() {} }, ...utils };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    return { ctx, removed };
}

test('first recorded sample enables legends/tooltips and clears placeholders', () => {
    const { ctx, removed } = sandbox();
    const a = fakeChart(), b = fakeChart();
    ctx.__a = a; ctx.__b = b;
    vm.runInContext('charts = { routing: __a, stageProgression: __b };', ctx);
    assert.strictEqual(vm.runInContext('chartsHaveSample', ctx), false);
    vm.runInContext("updateHistoricalData({ stage1_urls_discovered_total: 3 })", ctx);
    assert.strictEqual(vm.runInContext('chartsHaveSample', ctx), true);
    for (const c of [a, b]) {
        assert.strictEqual(c.options.plugins.legend.display, true);
        assert.strictEqual(c.options.plugins.tooltip.enabled, true);
    }
    assert.deepStrictEqual(removed.sort(), ['empty', 'ph']);
});

test('doughnutLegendLayout: right when wide, compact bottom when narrow', () => {
    assert.strictEqual(doughnutLegendLayout(724).position, 'right');
    assert.strictEqual(doughnutLegendLayout(480).position, 'right');
    const narrow = doughnutLegendLayout(352);
    assert.strictEqual(narrow.position, 'bottom');
    assert.ok(narrow.labels.boxWidth <= 10 && narrow.labels.font.size <= 11);
    assert.strictEqual(doughnutLegendLayout(undefined).position, 'bottom');
});

test('applyDoughnutLegend switches layout once and keeps legend visibility', () => {
    const { ctx } = sandbox();
    const c = fakeChart();
    c.options.plugins.legend.display = true;
    ctx.__c = c;
    vm.runInContext('applyDoughnutLegend(__c, 600)', ctx);
    assert.strictEqual(c.options.plugins.legend.position, 'right');
    assert.strictEqual(c.options.plugins.legend.display, true);
    assert.strictEqual(c.updates, 1);
    vm.runInContext('applyDoughnutLegend(__c, 610)', ctx); // same layout: no re-render
    assert.strictEqual(c.updates, 1);
    vm.runInContext('applyDoughnutLegend(__c, 300)', ctx);
    assert.strictEqual(c.options.plugins.legend.position, 'bottom');
    assert.strictEqual(c.updates, 2);
});
