// Run: node --test Scraping_project/dashboard/tests/
// #1012: updateDashboard records User Timing marks and warns when the poll's
// main-thread work exceeds 50ms. Idle polls must not force a layout per tile
// or redraw unchanged charts and data tables.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const utils = require('../format-utils.js');

function classList() {
    const set = new Set();
    return {
        set,
        add(c) { set.add(c); },
        remove(c) { set.delete(c); },
        toggle(c, on) { if (on) set.add(c); else set.delete(c); },
        contains(c) { return set.has(c); },
    };
}

function makePerf(measureDuration) {
    const marks = [];
    const measures = [];
    const cleared = [];
    return {
        marks,
        measures,
        cleared,
        now() { return 0; },
        mark(name) { marks.push(name); },
        measure(name, start, end) {
            measures.push({ name, start, end });
            if (typeof measureDuration === 'function') return measureDuration();
            if (measureDuration === undefined) return undefined;
            return { name, duration: measureDuration };
        },
        clearMarks(name) { cleared.push(['mark', name]); },
        clearMeasures(name) { cleared.push(['measure', name]); },
        getEntriesByName(name) {
            return measures.filter(m => m.name === name);
        },
    };
}

function sandbox(opts = {}) {
    const reads = { n: 0 };
    const nodes = Object.create(null);
    function flashing(id) {
        const el = { id, textContent: '', classList: classList() };
        Object.defineProperty(el, 'offsetWidth', { get() { reads.n += 1; return 8; } });
        nodes[id] = el;
        return el;
    }
    for (const id of opts.flash || []) flashing(id);
    for (const id of ['last-update', 'last-refresh-time', 'uptime']) {
        nodes[id] = { id, textContent: '' };
    }
    const warnings = [];
    const perf = opts.performance === undefined ? makePerf(1.5) : opts.performance;
    const document = {
        readyState: 'loading',
        title: 'Pipeline Control Center',
        addEventListener() {},
        getElementById(id) {
            if (Object.prototype.hasOwnProperty.call(opts.extra || {}, id)) return opts.extra[id];
            return nodes[id] || null;
        },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        createElement() { return { textContent: '', classList: classList(), children: [], appendChild(c) { this.children.push(c); } }; },
        createDocumentFragment() { return { appendChild() {} }; },
    };
    const ctx = {
        ...utils,
        document,
        window: {},
        console: {
            log() {},
            error() {},
            warn(msg) { warnings.push(String(msg)); },
        },
    };
    if (perf) ctx.performance = perf;
    vm.createContext(ctx);
    vm.runInContext(appSrc, ctx);
    return { ctx, warnings, reads, nodes, perf };
}

const SAMPLE = { stage1_urls_discovered_total: 1200, stage2_pages_analyzed_total: 3 };

test('updateDashboard records User Timing marks and stays quiet under 50ms (#1012)', () => {
    const { ctx, warnings, perf } = sandbox();
    vm.runInContext('updateDashboard(metrics)', Object.assign(ctx, { metrics: SAMPLE }));
    assert.deepStrictEqual(perf.marks, ['updateDashboard:start', 'updateDashboard:end']);
    assert.deepStrictEqual(perf.measures, [{
        name: 'updateDashboard',
        start: 'updateDashboard:start',
        end: 'updateDashboard:end',
    }]);
    assert.ok(perf.cleared.some(c => c[0] === 'mark' && c[1] === 'updateDashboard:start'));
    assert.ok(perf.cleared.some(c => c[0] === 'measure' && c[1] === 'updateDashboard'));
    assert.deepStrictEqual(warnings, []);
    assert.strictEqual(vm.runInContext('POLL_MAIN_THREAD_BUDGET_MS', ctx), 50);
});

test('console warns only when the measure is over the 50ms budget', () => {
    const over = sandbox({ performance: makePerf(62.4) });
    vm.runInContext('updateDashboard({})', over.ctx);
    assert.deepStrictEqual(over.warnings, [
        '[perf] updateDashboard main-thread work 62.4ms exceeds 50ms budget',
    ]);

    const exact = sandbox({ performance: makePerf(50) });
    vm.runInContext('updateDashboard({})', exact.ctx);
    assert.deepStrictEqual(exact.warnings, []);

    const fromBuffer = sandbox({
        performance: makePerf(() => undefined),
    });
    fromBuffer.perf.getEntriesByName = (name) => (
        name === 'updateDashboard' ? [{ name, duration: 80 }] : []
    );
    vm.runInContext('updateDashboard({})', fromBuffer.ctx);
    assert.match(fromBuffer.warnings[0], /80\.0ms exceeds 50ms budget/);
});

test('missing User Timing still updates and does not throw', () => {
    const { ctx } = sandbox({ performance: null });
    assert.doesNotThrow(() => vm.runInContext('updateDashboard({ stage1_urls_discovered_total: 4 })', ctx));
    assert.strictEqual(ctx.document.getElementById('uptime').textContent.length > 0, true);
});

test('changed metric tiles share one layout read (#1012)', () => {
    const ids = ['overview-s1-discovered', 'pipeline-s1-discovered'];
    const { ctx, reads, nodes } = sandbox({ flash: ids });
    ctx.metrics = { stage1_urls_discovered_total: 1200 };
    vm.runInContext('updateDashboard(metrics)', ctx);
    assert.strictEqual(reads.n, 1);
    for (const id of ids) {
        assert.strictEqual(nodes[id].textContent, '1.2K');
        assert.ok(nodes[id].classList.contains('flash'));
    }
    vm.runInContext('updateDashboard(metrics)', ctx);
    assert.strictEqual(reads.n, 1, 'unchanged text does not reflow');
    ctx.metrics = { stage1_urls_discovered_total: 2400 };
    vm.runInContext('updateDashboard(metrics)', ctx);
    assert.strictEqual(reads.n, 2);
    assert.strictEqual(nodes[ids[0]].textContent, '2.4K');
});

test('unchanged chart series are not redrawn', () => {
    const { ctx } = sandbox();
    function series(data) {
        return { data: { datasets: [{ data: data.slice() }] }, updates: 0, update() { this.updates += 1; } };
    }
    const stage = series([0, 0, 0, 0]);
    const routing = series([]);
    ctx.stage = stage;
    ctx.routing = routing;
    vm.runInContext('charts.stageProgression = stage; charts.routing = routing;', ctx);
    const metrics = {
        stage1_urls_discovered_total: 5,
        stage2_pages_analyzed_total: 1,
        stage3_summaries_created_total: 1,
        stage4_large_doc_summaries_total: 1,
        stage2_quality_docs_total: 2,
        stage2_massive_docs_total: 1,
    };
    ctx.metrics = metrics;
    vm.runInContext('updateDashboard(metrics)', ctx);
    assert.strictEqual(stage.updates, 1);
    assert.strictEqual(routing.updates, 1);
    vm.runInContext('updateDashboard(metrics)', ctx);
    assert.strictEqual(stage.updates, 1);
    assert.strictEqual(routing.updates, 1);
    ctx.metrics = Object.assign({}, metrics, { stage1_urls_discovered_total: 6 });
    vm.runInContext('updateDashboard(metrics)', ctx);
    assert.strictEqual(stage.updates, 2);
    assert.strictEqual(routing.updates, 1);
});

test('syncChartDataTable skips an unchanged table and rebuilds when values or legend visibility change', () => {
    let writes = 0;
    function host() {
        return { set innerHTML(v) { this.html = String(v); writes += 1; }, get innerHTML() { return this.html || ''; } };
    }
    const thead = host();
    const tbody = host();
    const table = {
        querySelector(sel) { return sel === 'thead' ? thead : sel === 'tbody' ? tbody : null; },
    };
    let hidden = false;
    const chart = {
        data: { labels: ['Stage 1'], datasets: [{ label: 'Documents', data: [4] }] },
        getDatasetMeta() { return { hidden }; },
    };
    const { ctx } = sandbox({ extra: { 'table-routing': table } });
    ctx.chart = chart;
    vm.runInContext('syncChartDataTable(chart, "table-routing")', ctx);
    assert.ok(writes >= 2);
    assert.match(tbody.html, />4</);
    const afterFirst = writes;
    vm.runInContext('syncChartDataTable(chart, "table-routing")', ctx);
    assert.strictEqual(writes, afterFirst);
    chart.data.datasets[0].data = [9];
    vm.runInContext('syncChartDataTable(chart, "table-routing")', ctx);
    assert.ok(writes > afterFirst);
    assert.match(tbody.html, />9</);
    const afterValue = writes;
    hidden = true;
    vm.runInContext('syncChartDataTable(chart, "table-routing")', ctx);
    assert.ok(writes > afterValue);
});
