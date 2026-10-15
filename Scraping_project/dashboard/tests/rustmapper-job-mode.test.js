// Stage 1 job mode copy for rustmapper degraded discovery (#941).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('path');
const { rustmapperDegradedModes, stageDrilldown } = require('../format-utils.js');

const MODES = ['abort', 'fallback_python', 'resume_data_dir'];
const REASONS = ['install_missing', 'crawl_empty', 'timeout_storm', 'oom', 'crash', 'sitemap_jsonl_missing'];

test('rustmapper degraded modes enumerate status strings and next actions', () => {
    const copy = rustmapperDegradedModes();
    assert.match(copy.intro, /Stage 1 job mode/);
    assert.match(copy.runbook, /rustmapper_discovery_degraded\.md$/);
    assert.deepStrictEqual(copy.modes.map(row => row.status), MODES);
    assert.deepStrictEqual(copy.reasons.map(row => row.status), REASONS);
    const modeNames = new Set(MODES);
    for (const row of copy.modes) {
        assert.ok(row.when.trim());
        assert.ok(row.nextAction.trim());
        assert.ok(row.label);
    }
    for (const row of copy.reasons) {
        assert.ok(modeNames.has(row.mode));
        assert.ok(row.nextAction.trim());
    }
    const again = rustmapperDegradedModes();
    again.modes[0].status = 'mutated';
    assert.strictEqual(rustmapperDegradedModes().modes[0].status, 'abort');
});

test('only the Stage 1 drilldown carries job mode copy', () => {
    const stage1 = stageDrilldown(1);
    assert.ok(stage1.jobMode);
    assert.deepStrictEqual(stage1.jobMode.modes.map(row => row.status), MODES);
    assert.strictEqual(stage1.links.length, 2);
    assert.deepStrictEqual(stage1.links.map(link => link.kind).sort(), ['jobs', 'queue']);
    for (const link of stage1.links) {
        assert.strictEqual(link.placeholder, true);
        assert.match(link.href, /^#/);
    }
    for (const stage of [2, 3, 4]) {
        const spec = stageDrilldown(stage);
        assert.strictEqual(spec.jobMode, null);
        assert.strictEqual(spec.links.length, 2);
    }
});

test('Control Center markup renders the Stage 1 job mode block', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(html, /id="stage-drilldown-job-mode"[^>]*hidden/);
    assert.match(app, /function renderStage1JobMode/);
    assert.match(app, /renderStage1JobMode\(spec\)/);
    assert.match(app, /title\.textContent = 'Stage 1 job mode'/);
    assert.match(app, /host\.hidden = true/);
    assert.match(app, /textContent = hintText\(row\)/);
    assert.doesNotMatch(app, /stage-drilldown-job-mode[\s\S]{0,400}innerHTML/);
});
