// Run: node --test Scraping_project/dashboard/tests/
// The activity feeds must not ship static seed entries ("Just now" /
// "System initialized") that app.js immediately replaces with different
// messages and real timestamps (#978).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

for (const id of ['overview-activity', 'activity-log']) {
    test(`#${id} is empty in HTML; app.js renders first paint (#978)`, () => {
        const m = html.match(new RegExp(`<div[^>]*id="${id}"[^>]*>([\\s\\S]*?)</div>`));
        assert.ok(m, `${id} container present`);
        assert.strictEqual(m[1].trim(), '', `${id} must not contain seed markup`);
    });
}

test('no conflicting static seed copy remains', () => {
    assert.ok(!/>\s*Just now\s*</.test(html));
    assert.ok(!/>\s*System initialized\s*</.test(html));
});
