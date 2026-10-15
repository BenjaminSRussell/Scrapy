// Run: node --test Scraping_project/dashboard/tests/
// #964: two series must be distinguishable without hue. Throughput pages/min
// is dashed with triangle markers. The massive doughnut slice is offset,
// dashed, and hatched when a canvas pattern can be built.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

const OLD_HUES = ['37, 99, 235', '124, 58, 237', '16, 185, 129', '245, 158, 11'];

function node(tag) {
    return {
        tagName: tag, id: '', className: '', textContent: '', children: [], attrs: {},
        classList: { add() {}, remove() {}, contains() { return false; } },
        setAttribute(k, v) { this.attrs[k] = v; },
        appendChild(c) { this.children.push(c); return c; },
        insertBefore(c) { this.children.unshift(c); return c; },
        querySelector() { return null; },
    };
}

function loadCharts(createElement) {
    const built = [];
    function Chart(el, config) {
        this.canvas = el;
        this.config = config;
        this.data = config.data;
        this.options = config.options;
        this.width = 640;
        built.push(this);
    }
    Chart.prototype.update = function () {};
    Chart.prototype.destroy = function () {};
    const document = {
        readyState: 'loading',
        addEventListener() {},
        body: node('body'),
        getElementById(id) { return node(id); },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        createElement,
    };
    const ctx = {
        document,
        window: {},
        console: { log() {}, warn() {}, error() {} },
        ...require('../format-utils.js'),
        Chart,
    };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    assert.strictEqual(vm.runInContext('initializeCharts()', ctx), true);
    const charts = vm.runInContext('charts', ctx);
    return { charts, built };
}

test('throughput and routing differ by dash, marker, and offset, not only hue (#964)', () => {
    const { charts } = loadCharts(() => node('canvas'));
    // Arrays built inside the vm do not share Array.prototype with this file.
    const asArray = (v) => Array.from(v);
    const lines = charts.throughput.data.datasets;
    assert.strictEqual(lines.length, 2);
    assert.deepStrictEqual(asArray(lines[0].borderDash), []);
    assert.strictEqual(lines[0].pointStyle, 'circle');
    assert.strictEqual(lines[0].borderColor, 'rgb(0, 114, 178)');
    assert.deepStrictEqual(asArray(lines[1].borderDash), [8, 4]);
    assert.strictEqual(lines[1].pointStyle, 'triangle');
    assert.strictEqual(lines[1].borderColor, 'rgb(213, 94, 0)');
    assert.notStrictEqual(lines[0].borderColor, lines[1].borderColor);
    assert.notStrictEqual(lines[0].pointStyle, lines[1].pointStyle);

    const ring = charts.routing.data.datasets[0];
    assert.deepStrictEqual(asArray(ring.offset), [0, 18]);
    assert.deepStrictEqual(asArray(ring.borderDash({ dataIndex: 0 })), []);
    assert.deepStrictEqual(asArray(ring.borderDash({ dataIndex: 1 })), [8, 4]);
    assert.deepStrictEqual(asArray(ring.borderColor), ['rgb(0, 114, 178)', 'rgb(230, 159, 0)']);
    assert.strictEqual(typeof ring.backgroundColor[0], 'string');
    assert.strictEqual(typeof ring.backgroundColor[1], 'string');

    const dumped = JSON.stringify(charts, (k, v) => (typeof v === 'function' ? undefined : v));
    for (const hue of OLD_HUES) assert.ok(!dumped.includes(hue), hue);
});

test('hatch fill is used for the massive slice and stages 2 and 4 when canvas exists', () => {
    let patterns = 0;
    function createElement(tag) {
        if (tag !== 'canvas') return node(tag);
        const g = {
            fillRect() {},
            beginPath() {},
            moveTo() {},
            lineTo() {},
            stroke() {},
            createPattern() {
                patterns += 1;
                return { pattern: 'hatch' };
            },
        };
        return { width: 0, height: 0, getContext() { return g; } };
    }
    const { charts } = loadCharts(createElement);
    const ring = charts.routing.data.datasets[0].backgroundColor;
    assert.strictEqual(typeof ring[0], 'string');
    assert.deepStrictEqual(ring[1], { pattern: 'hatch' });
    const bars = charts.stageProgression.data.datasets[0].backgroundColor;
    assert.strictEqual(typeof bars[0], 'string');
    assert.deepStrictEqual(bars[1], { pattern: 'hatch' });
    assert.strictEqual(typeof bars[2], 'string');
    assert.deepStrictEqual(bars[3], { pattern: 'hatch' });
    assert.strictEqual(patterns, 3);
});

test('chart captions state the non-hue encoding (#964)', () => {
    assert.match(html, /id="throughput-chart"[^>]*dashed line with triangle markers/);
    assert.match(html, /id="routing-chart"[^>]*hatched fill/);
    assert.match(html, /id="stage-progression-chart"[^>]*Stages 2 and 4 use a hatched fill/);
});
