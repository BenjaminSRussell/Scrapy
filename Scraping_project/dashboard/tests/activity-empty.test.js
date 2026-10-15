// Run: node --test Scraping_project/dashboard/tests/
// #961: an empty activity log shows a placeholder; new events replace it.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { ACTIVITY_EMPTY_COPY, activityEmptyCopy } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('empty log copy is the placeholder and a non-empty log is not', () => {
    assert.strictEqual(ACTIVITY_EMPTY_COPY, 'No activity yet');
    assert.strictEqual(activityEmptyCopy(0), 'No activity yet');
    assert.strictEqual(activityEmptyCopy(1), null);
    assert.strictEqual(activityEmptyCopy(50), null);
    assert.strictEqual(activityEmptyCopy(null), null);
    assert.strictEqual(activityEmptyCopy(undefined), null);
    assert.strictEqual(activityEmptyCopy('0'), null);
    assert.strictEqual(activityEmptyCopy(NaN), null);
});

test('feeds stay seed-free; app.js paints the placeholder and Clear restores it', () => {
    assert.match(html, /\.activity-empty\s*\{/);
    const forced = html.slice(html.indexOf('@media (forced-colors: active)'));
    assert.match(forced, /\.activity-empty\s*\{[^}]*border:\s*1px dashed CanvasText/);
    for (const id of ['overview-activity', 'activity-log']) {
        const m = html.match(new RegExp(`<div[^>]*id="${id}"[^>]*>([\\s\\S]*?)</div>`));
        assert.ok(m, `${id} container present`);
        assert.strictEqual(m[1].trim(), '', `${id} stays empty in HTML`);
    }
    assert.match(html, /id="clear-activity"/);
    const render = app.slice(app.indexOf('function renderActivityList'), app.indexOf('function updateActivityLog'));
    assert.match(render, /activityEmptyCopy\(activityLog\.length\)/);
    assert.match(render, /className = 'activity-empty'/);
    assert.match(render, /textContent = emptyCopy/);
    assert.match(render, /replaceChildren\(frag\)/);
    assert.ok(!render.includes('innerHTML'));
    const add = app.slice(app.indexOf('function addActivityLogItem'), app.indexOf('function announceActivity'));
    assert.match(add, /updateActivityLog\(\)/);
    const clear = app.slice(app.indexOf('function clearActivityLog'), app.indexOf('function detectSignificantChanges'));
    assert.match(clear, /activityLog\.length = 0/);
    assert.match(clear, /updateActivityLog\(\)/);
    assert.match(app, /activityLog\.length === 0/);
});
