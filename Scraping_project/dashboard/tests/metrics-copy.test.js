'use strict';
// #1019: copy the last parsed metrics snapshot JSON, with a toast.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { parseMetrics, metricsSnapshotJson, metricsCopyToast, copyMetricsSnapshot } = require('../format-utils.js');

test('metricsSnapshotJson pretty-prints a parsed snapshot and refuses a missing one', () => {
    assert.strictEqual(metricsSnapshotJson(null), null);
    assert.strictEqual(metricsSnapshotJson(undefined), null);
    assert.strictEqual(metricsSnapshotJson('nope'), null);
    assert.strictEqual(metricsSnapshotJson([]), null);
    const parsed = parseMetrics('stage1_urls_discovered_total 3\nfoo{a="b"} 1\n');
    const text = metricsSnapshotJson(parsed);
    const again = JSON.parse(text);
    assert.strictEqual(again.stage1_urls_discovered_total, 3);
    assert.strictEqual(again['foo{a="b"}'], 1);
    assert.match(text, /\n  "/);
    assert.strictEqual(metricsSnapshotJson({}), '{}');
});

test('copyMetricsSnapshot writes once and names the toast', async () => {
    const seen = [];
    const ok = await copyMetricsSnapshot({ n: 1 }, async (t) => { seen.push(t); });
    assert.strictEqual(ok.outcome, 'copied');
    assert.strictEqual(ok.message, 'Metrics snapshot copied');
    assert.strictEqual(ok.message, metricsCopyToast('copied'));
    assert.deepStrictEqual(JSON.parse(seen[0]), { n: 1 });
    assert.strictEqual(seen.length, 1);

    let writes = 0;
    const empty = await copyMetricsSnapshot(null, async () => { writes += 1; });
    assert.strictEqual(empty.outcome, 'empty');
    assert.strictEqual(empty.text, null);
    assert.strictEqual(empty.message, 'No metrics snapshot to copy yet');
    assert.strictEqual(writes, 0);

    const failed = await copyMetricsSnapshot({ n: 2 }, async () => { throw new Error('denied'); });
    assert.strictEqual(failed.outcome, 'failed');
    assert.strictEqual(failed.message, 'Could not copy metrics snapshot');
    assert.strictEqual(failed.text, metricsSnapshotJson({ n: 2 }));
    const missingWriter = await copyMetricsSnapshot({ n: 3 });
    assert.strictEqual(missingWriter.outcome, 'failed');
});

test('markup: copy button, clipboard write, and toast confirmation', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(html, /id="copy-metrics-json"[^>]*aria-label="Copy last metrics snapshot as JSON"/);
    assert.match(html, />Copy metrics JSON</);
    const toast = html.match(/<div id="metrics-copy-toast"[^>]*>/);
    assert.ok(toast, 'toast element present');
    for (const attr of ['role="status"', 'aria-live="polite"', 'aria-atomic="true"', 'hidden']) {
        assert.ok(toast[0].includes(attr), attr);
    }
    assert.match(html, /\.metrics-copy-toast\[hidden\]\s*\{\s*display:\s*none/);
    assert.match(app, /lastParsedMetrics = metrics/);
    assert.match(app, /getElementById\('copy-metrics-json'\)/);
    assert.match(app, /copyMetricsSnapshot\(lastParsedMetrics, writeClipboardText\)/);
    assert.match(app, /navigator\.clipboard\.writeText/);
    assert.match(app, /getElementById\('metrics-copy-toast'\)/);
    assert.match(app, /el\.textContent = message/);
});
