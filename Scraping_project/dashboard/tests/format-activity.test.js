// Run: node --test Scraping_project/dashboard/tests/
// #984: every activity line is formatActivity(type, template, params).
// Numbers use the shared formatter. Metric-derived text cannot carry raw tags.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { formatActivity, metricsFailureActivity } = require('../format-utils.js');

test('numbers use the safe formatter; non-finite counts are 0', () => {
    assert.deepStrictEqual(
        formatActivity('success', 'Stage 1: Discovered {count} new URL(s)', { count: 1500 }),
        { type: 'success', message: 'Stage 1: Discovered 1.5K new URL(s)' }
    );
    assert.strictEqual(
        formatActivity('success', 'Milestone: {count} {label}', { count: 1000, label: 'URLs discovered' }).message,
        'Milestone: 1.0K URLs discovered'
    );
    assert.strictEqual(
        formatActivity('warning', 'Stage 3: Deduplicated {count} document(s)', { count: NaN }).message,
        'Stage 3: Deduplicated 0 document(s)'
    );
    assert.strictEqual(
        formatActivity('info', 'n={count}', { count: Infinity }).message,
        'n=0'
    );
    assert.strictEqual(
        formatActivity('timeout', 'Metrics request timed out after {secs}s and was aborted', { secs: 4.5 }).message,
        'Metrics request timed out after 4.5s and was aborted'
    );
});

test('metric-derived text escapes angle brackets and keeps quotes (#984)', () => {
    const nasty = formatActivity('danger', 'Failed to fetch metrics: {detail}', {
        detail: '<script>alert(1)</script>',
    });
    assert.strictEqual(nasty.type, 'danger');
    assert.strictEqual(nasty.message, 'Failed to fetch metrics: &lt;script&gt;alert(1)&lt;/script&gt;');
    assert.ok(!/[<>]/.test(nasty.message));
    const again = formatActivity(nasty.type, nasty.message);
    assert.strictEqual(again.message, nasty.message, 'escaping is idempotent');

    const quoted = formatActivity('danger', '{detail}', {
        detail: 'Ignored ?metrics="javascript:alert(1)": only http(s)',
    });
    assert.strictEqual(quoted.message, 'Ignored ?metrics="javascript:alert(1)": only http(s)');
});

test('control characters are spaces; objects and unknown types cannot inject', () => {
    const line = formatActivity('nope!', 'x {detail}', { detail: 'a\n<b>\u0000c' });
    assert.strictEqual(line.type, 'info');
    assert.strictEqual(line.message, 'x a &lt;b&gt; c');
    assert.strictEqual(formatActivity('success', 'only {name}', { name: { html: '<i>' } }).message, 'only ');
    assert.strictEqual(formatActivity('success', 'keep {missing}', {}).message, 'keep {missing}');
    assert.strictEqual(formatActivity('warning', 'static <not a param>').message, 'static &lt;not a param&gt;');
});

test('metrics failures go through the same formatter (#767, #984)', () => {
    assert.deepStrictEqual(
        metricsFailureActivity(Object.assign(new Error('aborted'), { timedOut: true }), 4000),
        { type: 'timeout', message: 'Metrics request timed out after 4s and was aborted' }
    );
    assert.deepStrictEqual(
        metricsFailureActivity(new Error('HTTP 502'), 4000),
        { type: 'danger', message: 'Failed to fetch metrics: HTTP 502' }
    );
    const injected = metricsFailureActivity(new Error('<img src=x onerror=alert(1)>'), 4000);
    assert.strictEqual(injected.type, 'danger');
    assert.ok(!/[<>]/.test(injected.message));
    assert.match(injected.message, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test('every addActivityLogItem call site uses formatActivity (#984)', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    const calls = src.match(/addActivityLogItem\(/g) || [];
    const fed = src.match(/addActivityLogItem\(line\.type, line\.message\)/g) || [];
    const defs = src.match(/function addActivityLogItem/g) || [];
    assert.strictEqual(calls.length, fed.length + defs.length);
    assert.ok(fed.length >= 8);
    assert.strictEqual((src.match(/formatActivity\(/g) || []).length, fed.length);
    assert.ok(!src.includes('addActivityLogItem(`'), 'no raw template interpolation');
    assert.match(src, /formatActivity\('success', 'Stage 1: Discovered \{count\} new URL\(s\)'/);
    assert.match(src, /formatActivity\('danger', '\{detail\}', \{ detail: metricsProblem \}\)/);
});
