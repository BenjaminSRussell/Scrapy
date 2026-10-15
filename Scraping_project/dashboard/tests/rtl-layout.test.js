// Run: node --test Scraping_project/dashboard/tests/
// #1026: dir=rtl stress stub. Physical margin-left stays on the left;
// logical inline margins follow the writing direction.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('path');
const { tabKeyTarget } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');

function rule(selector, source = html) {
    const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const m = source.match(new RegExp(esc + '\\s*\\{([^}]*)\\}'));
    return m ? m[1] : null;
}

test('topbar badges use margin-inline-start, not margin-left (#1026)', () => {
    for (const sel of ['.env-badge', '.milestone-badge']) {
        const body = rule(sel);
        assert.ok(body, sel);
        assert.match(body, /margin-inline-start:\s*0\.\d+rem/);
        assert.ok(!/margin-left\s*:/.test(body), sel);
    }
});

test('tab overflow hint anchors to the inline end (#1026)', () => {
    const body = rule('.tab-buttons-wrap::after');
    assert.ok(body, 'fade rule present');
    assert.match(body, /inset-inline-end:\s*0/);
    assert.ok(!/\bright\s*:/.test(body), 'no physical right on the fade');
    assert.match(html, /\[dir="rtl"\]\s+\.tab-buttons-wrap::after\s*\{[^}]*to left/);
});

test('in-tab toolbar uses margin-inline-start (#1026)', () => {
    assert.match(html, /margin-inline-start:\s*auto/);
    assert.ok(!/margin-left:\s*auto/.test(html));
    assert.ok(!/margin-left\s*:/.test(html), 'no physical margin-left in the dashboard');
});

test('metric tables align to the writing direction (#1026)', () => {
    const body = rule('.data-fallback-table th, .data-fallback-table td');
    assert.ok(body);
    assert.match(body, /text-align:\s*start/);
    assert.ok(!/text-align:\s*left/.test(body));
});

test('activity stripe uses the inline-start edge (#1026)', () => {
    const body = rule('.activity-item');
    assert.match(body, /border-inline-start:\s*3px solid/);
    assert.ok(!/\.activity-item[^{]*\{[^}]*border-left/.test(html));
    assert.match(rule('.activity-item.timeout') || '', /border-inline-start-style:\s*dashed/);
});

test('RTL horizontal arrows follow the writing direction (#1026)', () => {
    assert.strictEqual(tabKeyTarget('ArrowLeft', 0, 5, true), 1);
    assert.strictEqual(tabKeyTarget('ArrowRight', 0, 5, true), 4);
    assert.strictEqual(tabKeyTarget('ArrowRight', 4, 5, true), 3);
    assert.strictEqual(tabKeyTarget('Left', 1, 5, true), 2);
    assert.strictEqual(tabKeyTarget('ArrowRight', 0, 5, false), 1);
    assert.strictEqual(tabKeyTarget('ArrowRight', 0, 5), 1, 'default stays LTR');
    assert.strictEqual(tabKeyTarget('ArrowLeft', 0, 5), 4);
    assert.strictEqual(tabKeyTarget('Home', 3, 5, true), 0);
    assert.strictEqual(tabKeyTarget('End', 1, 5, true), 4);
    assert.match(app, /getComputedStyle\(tablist\)\.direction === 'rtl'/);
    assert.match(app, /tabKeyTarget\(event\.key,\s*current,\s*tabs\.length,\s*rtl\)/);
});

test('README documents the dir=rtl smoke test (#1026)', () => {
    assert.match(readme, /document\.documentElement\.dir = 'rtl'/);
    assert.match(readme, /margin-inline-start/);
    assert.match(readme, /Topbar:/);
    assert.match(readme, /Tabs:/);
    assert.match(readme, /Metrics:/);
});
