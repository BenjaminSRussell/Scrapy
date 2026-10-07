// Run: node --test Scraping_project/dashboard/tests/
const test = require('node:test');
const assert = require('node:assert');
const { parseMetrics, formatNumber, formatBytes } = require('../format-utils.js');

test('parseMetrics skips comments and parses values', () => {
    const m = parseMetrics('# HELP x\nfoo_total 12\nbar{a="b"} 3.5\n\nbad line');
    assert.strictEqual(m.foo_total, 12);
    assert.strictEqual(m['bar{a="b"}'], 3.5);
    assert.ok(!('bad' in m));
});

test('formatNumber thresholds', () => {
    assert.strictEqual(formatNumber(999), '999');
    assert.strictEqual(formatNumber(1500), '1.5K');
    assert.strictEqual(formatNumber(2500000), '2.5M');
});

test('formatBytes thresholds', () => {
    assert.strictEqual(formatBytes(512), '512 B');
    assert.strictEqual(formatBytes(2048), '2.00 KB');
    assert.strictEqual(formatBytes(3 * 1048576), '3.00 MB');
    assert.strictEqual(formatBytes(1073741824), '1.00 GB');
});
