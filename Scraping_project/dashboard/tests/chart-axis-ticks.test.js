// Run: node --test Scraping_project/dashboard/tests/
// (#965) Cartesian y-axes use formatNumber (K/M). Tooltips keep the full value.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { formatNumber, formatPreciseNumber } = require('../format-utils.js');
const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function node(tag) {
    return {
        tagName: tag, id: '', className: '', textContent: '', children: [], attrs: {},
        classList: { add() {}, remove() {}, contains() { return false; } },
        setAttribute() {},
        appendChild(c) { this.children.push(c); return c; },
        insertBefore(c) { this.children.unshift(c); return c; },
        querySelector() { return null; },
        get firstChild() { return this.children[0] || null; },
    };
}

test('formatPreciseNumber keeps the full value', () => {
    assert.strictEqual(formatPreciseNumber(1500), '1,500');
    assert.strictEqual(formatPreciseNumber(2500000), '2,500,000');
    assert.strictEqual(formatPreciseNumber(999), '999');
    assert.strictEqual(formatPreciseNumber(12.5), '12.5');
    assert.strictEqual(formatPreciseNumber(null), '');
    assert.ok(!/K|M/.test(formatPreciseNumber(2500000)));
    assert.strictEqual(formatNumber(2500000), '2.5M');
});

test('cartesian y ticks use formatNumber; tooltips stay precise; doughnut has no y axis', () => {
    const built = [];
    function Chart(canvas, config) {
        this.canvas = canvas;
        this.options = config.options;
        this.width = 640;
        this.update = () => {};
        this.destroy = () => {};
        built.push(this);
    }
    const document = {
        readyState: 'loading',
        addEventListener() {},
        body: node('body'),
        getElementById(id) { return id && id.endsWith('-chart') ? node('canvas') : node('div'); },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        createElement: node,
    };
    const ctx = { ...require('../format-utils.js'), document, window: {}, console: { log() {}, warn() {}, error() {} }, Chart };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    assert.strictEqual(vm.runInContext('initializeCharts()', ctx), true);
    assert.strictEqual(built.length, 6);

    const routing = built[2];
    assert.ok(!routing.options.scales, 'doughnut has no axes');

    for (const chart of built) {
        if (chart === routing) continue;
        const tick = chart.options.scales.y.ticks.callback;
        assert.strictEqual(tick(999), '999');
        assert.strictEqual(tick(1500), '1.5K');
        assert.strictEqual(tick(2500000), '2.5M');
        const label = chart.options.plugins.tooltip.callbacks.label;
        assert.strictEqual(label({ parsed: { y: 2500000 }, dataset: { label: 'URLs Discovered' } }), 'URLs Discovered: 2,500,000');
        assert.strictEqual(label({ parsed: { y: 1500 }, dataset: { label: 'Pages/min' } }), 'Pages/min: 1,500');
        assert.ok(!/K|M/.test(label({ parsed: { y: 2500000 }, dataset: { label: 'Summaries Created' } })));
        assert.strictEqual(chart.options.plugins.tooltip.enabled, false);
    }
});
