// Run: node --test Scraping_project/dashboard/tests/
// Pipeline stage cards' `active` class must track live rates, not be
// hardcoded on stages 1-2 (#976).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { stageActivity } = require('../format-utils.js');

test('stageActivity maps rates to the four stage cards in order', () => {
    assert.deepStrictEqual(stageActivity({ urls: 0, pages: 2, summaries: 0, largeDocs: 0.1 }),
        [false, true, false, true]);
    assert.deepStrictEqual(stageActivity({ urls: 12, pages: 0, summaries: 0.5, largeDocs: 0 }),
        [true, false, true, false]);
});

test('missing, NaN, negative (counter reset) and zero rates are idle', () => {
    assert.deepStrictEqual(stageActivity({ urls: NaN, pages: undefined, summaries: -3, largeDocs: 0 }),
        [false, false, false, false]);
    assert.deepStrictEqual(stageActivity(undefined), [false, false, false, false]);
});

test('threshold is respected', () => {
    assert.deepStrictEqual(stageActivity({ urls: 1, pages: 5, summaries: 0, largeDocs: 0 }, 2),
        [false, true, false, false]);
});

test('HTML no longer hardcodes active stage cards', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    assert.ok(!/class="stage-card active"/.test(html));
    assert.strictEqual((html.match(/class="stage-card"/g) || []).length, 4);
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(app, /applyStageActivity\(rates\)/);
});
