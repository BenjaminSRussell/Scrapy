// Run: node --test Scraping_project/dashboard/tests/
// #1038: the Settings panel must list the series app.js actually reads.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { METRIC_UI_MAP, renderMetricNameMap } = require('../format-utils.js');

const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

function seriesReadByApp(src) {
    const keys = new Set();
    const patterns = [
        /metrics\[\s*['"]([A-Za-z_:][A-Za-z0-9_:]*)['"]\s*\]/g,
        /['"]([A-Za-z_:][A-Za-z0-9_:]*)['"]\s+in\s+metrics/g,
        /countLabelValues\(\s*metrics\s*,\s*['"]([A-Za-z_:][A-Za-z0-9_:]*)['"]/g,
    ];
    for (const re of patterns) {
        for (const m of src.matchAll(re)) keys.add(m[1]);
    }
    return keys;
}

function fakeElement(tag) {
    return {
        tagName: tag,
        className: '',
        id: '',
        textContent: '',
        children: [],
        appendChild(child) { this.children.push(child); return child; },
        removeChild(child) {
            const i = this.children.indexOf(child);
            if (i >= 0) this.children.splice(i, 1);
            return child;
        },
        get firstChild() { return this.children[0] || null; },
    };
}

test('METRIC_UI_MAP covers every series app.js reads (#1038)', () => {
    const listed = new Set(METRIC_UI_MAP.map(row => row.metric));
    const missing = [...seriesReadByApp(app)].filter(name => !listed.has(name));
    assert.deepStrictEqual(missing, []);
    assert.ok(listed.has('stage1_urls_discovered_total'));
    assert.ok(listed.has('delta_lake_records'));
    assert.ok(listed.has('pipeline_running'));
});

test('every mapped element id exists in the dashboard markup', () => {
    const ids = new Set();
    for (const row of METRIC_UI_MAP) {
        assert.ok(row.metric, 'metric name');
        assert.ok(row.read, row.metric);
        assert.ok(row.fields.length > 0, row.metric);
        for (const field of row.fields) {
            assert.match(field.label, /\S/);
            assert.match(field.id, /^[a-z0-9-]+$/);
            ids.add(field.id);
        }
    }
    for (const id of ids) {
        assert.ok(html.includes(`id="${id}"`), `missing #${id}`);
    }
});

test('renderMetricNameMap writes one row per series with its fields', () => {
    const root = fakeElement('div');
    renderMetricNameMap(root, { createElement: fakeElement });
    const table = root.children[0];
    assert.strictEqual(table.id, 'metric-name-map-table');
    const tbody = table.children.find(el => el.tagName === 'tbody');
    assert.strictEqual(tbody.children.length, METRIC_UI_MAP.length);
    const rendered = tbody.children.map(tr => tr.children[0].children[0].textContent);
    assert.deepStrictEqual(rendered, METRIC_UI_MAP.map(row => row.metric));
    const stage4 = tbody.children.find(tr => tr.children[0].children[0].textContent === 'stage4_large_doc_summaries_total');
    const fieldText = stage4.children[1].children[0].children.map(li => li.textContent).join('\n');
    assert.match(fieldText, /pipeline-s4-summaries/);
    assert.doesNotMatch(fieldText, /spark-overview-s4/);
});

test('System tab Settings panel is the metric-name map host', () => {
    assert.match(html, /id="metric-name-map"/);
    assert.match(html, /id="metric-name-map-title"[^>]*>Settings</);
    assert.match(html, /<summary[^>]*>Prometheus metric name map<\/summary>/);
    assert.match(html, /id="metric-name-map-body"/);
    assert.match(app, /renderMetricNameMap\(document\.getElementById\('metric-name-map-body'\), document\)/);
    const system = html.slice(html.indexOf('id="tab-system"'), html.indexOf('id="tab-activity"'));
    assert.match(system, /id="metric-name-map"/);
});
