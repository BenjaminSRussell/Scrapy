// Run: node --test Scraping_project/dashboard/tests/
// (#666) Overlapping metrics fetches apply only the latest snapshot, and a
// burst of samples before the next paint schedules one Chart.js update.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const utils = require('../format-utils.js');

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function makeNode(tag) {
    const node = {
        tagName: tag || 'DIV',
        id: '',
        className: '',
        textContent: '',
        innerHTML: '',
        hidden: false,
        title: '',
        value: '',
        style: { display: '' },
        attrs: {},
        dataset: {},
        children: [],
        parent: null,
        offsetWidth: 1,
        scrollTop: 0,
        clientHeight: 0,
        scrollHeight: 0,
        setAttribute(k, v) { this.attrs[k] = String(v); },
        getAttribute(k) { return this.attrs[k]; },
        removeAttribute(k) { delete this.attrs[k]; },
        appendChild(c) { if (c) c.parent = this; this.children.push(c); return c; },
        insertBefore(c) { if (c) c.parent = this; this.children.unshift(c); return c; },
        append(...cs) { cs.forEach((c) => this.appendChild(c)); },
        replaceChildren(...cs) {
            this.children.forEach((c) => { if (c) c.parent = null; });
            this.children = [];
            cs.flat().forEach((c) => this.appendChild(c));
        },
        remove() {
            const p = this.parent;
            if (!p) return;
            const i = p.children.indexOf(this);
            if (i >= 0) p.children.splice(i, 1);
            this.parent = null;
        },
        addEventListener() {},
        querySelector(sel) {
            if (sel === 'thead' || sel === 'tbody') return makeNode(sel);
            if (sel === 'span:last-child' || sel === '.health-value') return makeNode('span');
            return null;
        },
        querySelectorAll() { return []; },
        get childElementCount() { return this.children.length; },
        get firstElementChild() { return this.children[0] || null; },
        get firstChild() { return this.children[0] || null; },
    };
    node.classList = {
        add() {},
        remove() {},
        contains() { return false; },
        toggle() {},
    };
    return node;
}

function fakeChart() {
    return {
        modes: [],
        update(mode) { this.modes.push(mode); },
        data: { labels: [], datasets: [{ data: [], label: 'a' }, { data: [], label: 'b' }] },
        options: { plugins: { legend: { display: false, position: 'bottom' }, tooltip: { enabled: false } } },
        width: 640,
    };
}

function sandbox() {
    const nodes = new Map();
    const frames = [];
    const errors = [];
    const document = {
        readyState: 'loading',
        title: 'Pipeline Control Center',
        body: makeNode('body'),
        addEventListener() {},
        createElement: (tag) => makeNode(tag),
        createTextNode: (text) => ({ textContent: String(text), nodeType: 3, parent: null, remove() {} }),
        createDocumentFragment: () => makeNode('fragment'),
        getElementById(id) {
            if (!nodes.has(id)) {
                const n = makeNode('div');
                n.id = id;
                nodes.set(id, n);
            }
            return nodes.get(id);
        },
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
    const ctx = {
        ...utils,
        document,
        window: {},
        console: {
            log() {},
            warn() {},
            error(...args) { errors.push(args.map(String).join(' ')); },
        },
        setTimeout,
        clearTimeout,
        AbortController,
        requestAnimationFrame(fn) { frames.push(fn); return frames.length; },
    };
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    const charts = {
        urls: fakeChart(),
        pages: fakeChart(),
        summaries: fakeChart(),
        throughput: fakeChart(),
        stageProgression: fakeChart(),
        routing: fakeChart(),
    };
    ctx.__charts = charts;
    vm.runInContext('charts = __charts;', ctx);
    return { ctx, document, nodes, frames, errors, charts };
}

function snap(n) {
    return {
        stage1_urls_discovered_total: n,
        stage1_urls_queued_total: 0,
        stage2_pages_analyzed_total: 2,
        stage2_quality_docs_total: 1,
        stage2_massive_docs_total: 0,
        stage3_summaries_created_total: 1,
        stage4_large_doc_summaries_total: 1,
    };
}

function flush(frames) {
    const queued = frames.splice(0, frames.length);
    for (const fn of queued) fn();
}

test('app.js header documents the coalesce policy (#666)', () => {
    const header = appSrc.slice(0, appSrc.indexOf('const METRICS_URL'));
    assert.match(header, /Coalesce policy \(#666\)/);
    assert.match(header, /latest snapshot/);
    assert.match(header, /requestAnimationFrame/);
    assert.match(header, /does not stack chart updates/);
});

test('artificial 100ms flood paints each chart once with the latest snapshot (#666)', () => {
    const { ctx, frames, errors, charts } = sandbox();
    vm.runInContext('updateDashboard(__snap)', Object.assign(ctx, { __snap: snap(3) }));
    flush(frames);
    for (const ch of Object.values(charts)) ch.modes.length = 0;
    assert.strictEqual(frames.length, 0);

    for (let i = 0; i < 10; i++) {
        ctx.__snap = snap(10 + i);
        vm.runInContext('updateDashboard(__snap)', ctx);
    }
    assert.strictEqual(frames.length, 1, 'one frame for the whole burst, not one per sample');
    for (const ch of Object.values(charts)) {
        assert.deepStrictEqual(ch.modes, [], ch === charts.throughput ? 'throughput' : 'chart');
    }
    flush(frames);
    for (const [name, ch] of Object.entries(charts)) {
        assert.deepStrictEqual(ch.modes, ['none'], name);
    }
    // Chart data arrays are created inside the vm; copy them out before compare.
    assert.deepStrictEqual([...charts.stageProgression.data.datasets[0].data], [19, 2, 1, 1]);
    assert.deepStrictEqual([...charts.routing.data.datasets[0].data], [1, 0]);
    assert.strictEqual(errors.length, 0, errors.join('\n'));
});

function prom(urls) {
    return [
        `stage1_urls_discovered_total ${urls}`,
        'stage2_pages_analyzed_total 2',
        'stage3_summaries_created_total 1',
        'stage4_large_doc_summaries_total 1',
    ].join('\n') + '\n';
}

async function startFetch(ctx, pending) {
    ctx.fetch = (url, init) => new Promise((resolve) => {
        pending.push({ resolve, init });
    });
    const pendingBefore = pending.length;
    // Wrap the task: returning the vm promise from this async function would
    // wait for the fetch, which the caller has not resolved yet.
    const task = vm.runInContext('fetchMetrics()', ctx);
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
    assert.ok(pending.length > pendingBefore, 'fetch started');
    return { task };
}

function history(ctx) {
    return JSON.parse(vm.runInContext('JSON.stringify(historicalData.urls)', ctx));
}

function body(text) {
    return { ok: true, status: 200, statusText: 'OK', text: async () => text };
}

test('overlapping fetches apply only the latest snapshot (#666)', async () => {
    const { ctx, nodes, errors } = sandbox();
    const pending = [];
    const older = (await startFetch(ctx, pending)).task;
    const newer = (await startFetch(ctx, pending)).task;
    assert.strictEqual(pending.length, 2);

    // The older response arrives first and must not paint.
    pending[0].resolve(body(prom(1)));
    await older;
    const staleUrls = nodes.get('topbar-urls');
    assert.ok(!staleUrls || String(staleUrls.textContent) !== '1');
    assert.deepStrictEqual(history(ctx), []);

    pending[1].resolve(body(prom(9)));
    await newer;
    assert.strictEqual(String(nodes.get('topbar-urls').textContent), '9');
    assert.deepStrictEqual(history(ctx), [9]);
    assert.strictEqual(errors.length, 0, errors.join('\n'));
});

test('a late stale snapshot does not overwrite a newer one (#666)', async () => {
    const { ctx, nodes, errors } = sandbox();
    const pending = [];
    const older = (await startFetch(ctx, pending)).task;
    const newer = (await startFetch(ctx, pending)).task;

    pending[1].resolve(body(prom(9)));
    await newer;
    assert.strictEqual(String(nodes.get('topbar-urls').textContent), '9');

    pending[0].resolve(body(prom(1)));
    await older;
    assert.strictEqual(String(nodes.get('topbar-urls').textContent), '9');
    assert.deepStrictEqual(history(ctx), [9]);
    assert.strictEqual(String(nodes.get('topbar-status').textContent).includes('Online'), true);
    assert.strictEqual(errors.length, 0, errors.join('\n'));
});
