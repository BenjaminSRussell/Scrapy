// Run: node --test Scraping_project/dashboard/tests/
// (#1018) Each chart card downloads its canvas as a PNG with a UTC timestamp
// in the filename, via canvas.toDataURL('image/png').
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

const CHARTS = [
    ['throughput-chart', 'Pipeline Throughput'],
    ['stage-progression-chart', 'Stage Progression'],
    ['routing-chart', 'Document Routing'],
    ['urls-chart', 'URLs Discovered Over Time'],
    ['pages-chart', 'Pages Analyzed Over Time'],
    ['summaries-chart', 'Summaries Created Over Time'],
];

test('every chart canvas has one Download PNG button', () => {
    const canvases = [...html.matchAll(/<canvas id="([a-z0-9-]+)"/g)].map(m => m[1]);
    assert.deepStrictEqual(canvases, CHARTS.map(c => c[0]));
    for (const [id, title] of CHARTS) {
        const re = new RegExp(
            `<button type="button" class="btn chart-png-btn" data-chart-export="${id}" aria-label="Download ${title} chart as PNG">Download PNG</button>`
        );
        assert.match(html, re, id);
    }
    assert.strictEqual((html.match(/data-chart-export="/g) || []).length, CHARTS.length);
});

function sandbox() {
    const anchors = [];
    const clicks = [];
    const live = { id: 'refresh-status', textContent: '' };
    const canvases = {};
    function node(tag) {
        return {
            tagName: tag, id: '', href: '', download: '', rel: '', textContent: '',
            children: [], attrs: {},
            setAttribute(k, v) { this.attrs[k] = String(v); },
            getAttribute(k) { return this.attrs[k]; },
            addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
            listeners: {},
            appendChild(c) { this.children.push(c); return c; },
            remove() { this.removed = true; },
            click() { this.clicked = true; clicks.push(this); },
        };
    }
    const body = node('body');
    const buttons = CHARTS.map(([id]) => {
        const btn = node('button');
        btn.setAttribute('data-chart-export', id);
        return btn;
    });
    const document = {
        readyState: 'loading',
        body,
        addEventListener() {},
        createElement(tag) {
            const n = node(tag);
            if (tag === 'a') anchors.push(n);
            return n;
        },
        getElementById(id) {
            if (id === 'refresh-status') return live;
            return canvases[id] || null;
        },
        querySelector() { return null; },
        querySelectorAll(sel) {
            if (sel === '[data-chart-export]') return buttons;
            return [];
        },
    };
    const ctx = {
        ...require('../format-utils.js'),
        document,
        window: {},
        console: { log() {}, warn() {}, error() {} },
    };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    return { ctx, anchors, clicks, live, canvases, buttons };
}

test('chartPngFilename is a UTC timestamped png and strips odd canvas ids', () => {
    const { ctx } = sandbox();
    const when = 'new Date("2026-10-14T23:30:07.123Z")';
    assert.strictEqual(
        vm.runInContext(`chartPngFilename("throughput-chart", ${when})`, ctx),
        'throughput-chart-20261014T233007Z.png'
    );
    assert.strictEqual(
        vm.runInContext(`chartPngFilename("weird id/../x", ${when})`, ctx),
        'weird-id-x-20261014T233007Z.png'
    );
    assert.match(vm.runInContext('chartPngFilename("", new Date(NaN))', ctx), /^chart-\d{8}T\d{6}Z\.png$/);
});

test('downloadChartPng uses canvas.toDataURL and saves a timestamped png', () => {
    const { ctx, anchors, live, canvases } = sandbox();
    const seen = [];
    canvases['urls-chart'] = {
        id: 'urls-chart',
        toDataURL(type) {
            seen.push(type);
            return 'data:image/png;base64,iVBORw0KGgo=';
        },
    };
    const name = vm.runInContext(
        'downloadChartPng(document.getElementById("urls-chart"), new Date("2026-10-14T23:30:07.000Z"))',
        ctx
    );
    assert.strictEqual(name, 'urls-chart-20261014T233007Z.png');
    assert.deepStrictEqual(seen, ['image/png']);
    assert.strictEqual(anchors.length, 1);
    assert.strictEqual(anchors[0].href, 'data:image/png;base64,iVBORw0KGgo=');
    assert.strictEqual(anchors[0].download, name);
    assert.strictEqual(anchors[0].rel, 'noopener');
    assert.strictEqual(anchors[0].clicked, true);
    assert.strictEqual(anchors[0].removed, true);
    assert.strictEqual(live.textContent, `Downloaded ${name}`);
});

test('downloadChartPng does not save when the canvas cannot be read', () => {
    const { ctx, anchors, live, canvases } = sandbox();
    canvases['pages-chart'] = {
        id: 'pages-chart',
        toDataURL() { throw new Error('tainted'); },
    };
    assert.strictEqual(vm.runInContext('downloadChartPng(document.getElementById("pages-chart"))', ctx), null);
    assert.strictEqual(vm.runInContext('downloadChartPng(null)', ctx), null);
    assert.strictEqual(anchors.length, 0);
    assert.strictEqual(live.textContent, 'Chart PNG unavailable');
});

test('Download PNG click exports the canvas named on the button', () => {
    const { ctx, anchors, buttons, canvases } = sandbox();
    canvases['routing-chart'] = {
        id: 'routing-chart',
        toDataURL() { return 'data:image/png;base64,AAAA'; },
    };
    vm.runInContext('setupChartPngExports()', ctx);
    const routing = buttons.find(b => b.getAttribute('data-chart-export') === 'routing-chart');
    assert.strictEqual(routing.listeners.click.length, 1);
    routing.listeners.click[0]();
    assert.strictEqual(anchors.length, 1);
    assert.match(anchors[0].download, /^routing-chart-\d{8}T\d{6}Z\.png$/);
    assert.strictEqual(anchors[0].href, 'data:image/png;base64,AAAA');
});
