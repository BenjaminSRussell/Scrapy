// #460: JS render budget. The estimate is a pure function; the card only displays it.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { jsBudgetSplit, jsRenderBudget, JS_PRESET_PAGES } = require('../format-utils.js');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');

test('preset split never exceeds the operator page cap', () => {
    for (const preset of ['default', 'low_memory']) {
        for (const total of [1, 2, 3, 4, 5, 7, 16, 32, 100]) {
            const { contexts, pagesPerContext } = jsBudgetSplit(preset, total);
            assert.ok(contexts >= 1 && pagesPerContext >= 1);
            assert.ok(contexts * pagesPerContext <= total, `${preset} ${total}`);
        }
    }
});

test('default cap of 32 pages is 4 contexts x 8 pages, as in the spider', () => {
    assert.deepStrictEqual(jsBudgetSplit('default', 32), { contexts: 4, pagesPerContext: 8 });
    assert.strictEqual(JS_PRESET_PAGES.default, 32);
    assert.strictEqual(JS_PRESET_PAGES.low_memory, 4);
});

test('estimate is pages x MB per page and warns only above the limit', () => {
    const fits = jsRenderBudget('default', 32, 100, 4096);
    assert.strictEqual(fits.pages, 32);
    assert.strictEqual(fits.estimateMb, 3200);
    assert.strictEqual(fits.over, false);
    assert.match(fits.message, /^Estimated 3,200 MB for 32 pages, within the 4,096 MB limit/);

    const over = jsRenderBudget('default', 32, 200, 4096);
    assert.strictEqual(over.estimateMb, 6400);
    assert.strictEqual(over.over, true);
    assert.match(over.message, /^Warning: estimated 6,400 MB/);
});

test('low_memory preset stays well under the limit at its default cap', () => {
    const view = jsRenderBudget('low_memory', JS_PRESET_PAGES.low_memory, 100);
    assert.strictEqual(view.pages, 4);
    assert.strictEqual(view.over, false);
});

test('bad or missing inputs fall back to safe values', () => {
    const view = jsRenderBudget('nonsense', 'abc', '');
    assert.strictEqual(view.contexts, 4);
    assert.strictEqual(view.pages, 32);
    assert.strictEqual(view.estimateMb, 3200);
    assert.strictEqual(view.limitMb, 4096);
    assert.strictEqual(view.snippet, 'PLAYWRIGHT_MAX_CONCURRENT_PAGES=32');
});

test('snippet names the env var the spider reads', () => {
    assert.strictEqual(jsRenderBudget('default', 12, 100).snippet, 'PLAYWRIGHT_MAX_CONCURRENT_PAGES=12');
});

test('budget card is present, labelled, and states it does not apply changes', () => {
    assert.match(html, /id="js-budget-card"/);
    assert.match(html, /<select id="js-budget-preset"/);
    assert.match(html, /<input type="number" id="js-budget-pages"/);
    assert.match(html, /<input type="number" id="js-budget-mb"/);
    assert.match(html, /Changing these fields does not change the running spider/);
    assert.match(html, /role="status"[^>]*id="js-budget-result"|id="js-budget-result"[^>]*role="status"/);
});

test('the estimate card is wired in app.js', () => {
    assert.match(app, /function setupJsBudget\(\)/);
    assert.match(app, /setupJsBudget\(\);/);
    assert.doesNotMatch(app, /localStorage\.setItem\(['"]js-budget/);
});
