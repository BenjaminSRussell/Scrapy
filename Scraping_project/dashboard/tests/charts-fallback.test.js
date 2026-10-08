// Run: node --test Scraping_project/dashboard/tests/
// Chart.js is loaded from a CDN; when it is blocked, initializeCharts must not
// throw (which aborted initialize() and the metrics poll) and must show a
// visible notice instead (#983). Loads the real app.js in a vm sandbox with a
// tiny DOM stub.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function node(tag) {
    const n = {
        tagName: tag, id: '', className: '', textContent: '', children: [], attrs: {},
        classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, contains(c) { return this.set.has(c); } },
        setAttribute(k, v) { this.attrs[k] = v; },
        appendChild(c) { this.children.push(c); return c; },
        insertBefore(c) { this.children.unshift(c); return c; },
        querySelector(sel) { return this.children.find(c => '.' + c.className === sel) || null; },
        get firstChild() { return this.children[0] || null; },
    };
    return n;
}

function sandbox(Chart) {
    const main = node('main');
    const containers = [node('div'), node('div'), node('div')];
    const canvases = {};
    const document = {
        readyState: 'loading',
        addEventListener() {},
        body: node('body'),
        getElementById(id) {
            if (id === 'main') return main;
            if (id.endsWith('-chart')) return (canvases[id] = canvases[id] || node('canvas'));
            return main.children.find(c => c.id === id) || null;
        },
        querySelector() { return null; },
        querySelectorAll(sel) { return sel === '.chart-container' ? containers : []; },
        createElement: node,
    };
    const ctx = { document, window: {}, console: { log() {}, warn() {}, error() {} } };
    if (Chart) ctx.Chart = Chart;
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    return { ctx, main, containers };
}

test('Chart.js missing: initializeCharts degrades with a visible notice (#983)', () => {
    const { ctx, main, containers } = sandbox(undefined);
    let ok;
    assert.doesNotThrow(() => { ok = vm.runInContext('initializeCharts()', ctx); });
    assert.strictEqual(ok, false);
    const banner = main.children.find(c => c.id === 'charts-unavailable');
    assert.ok(banner, 'banner inserted');
    assert.match(banner.textContent, /Charts unavailable/);
    assert.strictEqual(banner.attrs.role, 'status');
    for (const c of containers) {
        assert.ok(c.classList.contains('is-unavailable'));
        assert.ok(c.querySelector('.chart-unavailable-note'));
    }
    assert.strictEqual(vm.runInContext('Object.keys(charts).length', ctx), 0);
});

test('Chart constructor throwing mid-way: partial charts destroyed, notice shown', () => {
    let made = 0, destroyed = 0;
    function Chart() {
        if (++made === 3) throw new Error('canvas unsupported');
        this.destroy = () => { destroyed++; };
    }
    const { ctx, main } = sandbox(Chart);
    assert.strictEqual(vm.runInContext('initializeCharts()', ctx), false);
    assert.strictEqual(destroyed, 2);
    assert.strictEqual(vm.runInContext('Object.keys(charts).length', ctx), 0);
    assert.ok(main.children.find(c => c.id === 'charts-unavailable'));
});

test('Chart.js present: charts built, no notice', () => {
    function Chart() {}
    Chart.prototype.destroy = function () {};
    const { ctx, main } = sandbox(Chart);
    assert.strictEqual(vm.runInContext('initializeCharts()', ctx), true);
    assert.strictEqual(vm.runInContext('Object.keys(charts).length', ctx), 6);
    assert.strictEqual(main.children.length, 0);
});
