'use strict';
// #556: S starts a job (only while Start is enabled), P pauses or resumes
// auto-refresh, E and C export the activity log. None of them moves focus.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { shortcutAction, jobStartShortcutDecision } = require('../format-utils.js');

const TABS = ['overview', 'pipeline', 'performance', 'system', 'activity'];
const key = (k, extra = {}) => ({ key: k, target: { tagName: 'BODY' }, ...extra });
const readDashboardFile = (name) => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
const app = readDashboardFile('app.js');
const readme = readDashboardFile('README.md');

test('S, P, E and C map to start, pause and the two exports (#556)', () => {
    assert.deepStrictEqual(shortcutAction(key('s'), TABS), { type: 'job-start' });
    assert.deepStrictEqual(shortcutAction(key('S', { shiftKey: true }), TABS), { type: 'job-start' });
    assert.deepStrictEqual(shortcutAction(key('p'), TABS), { type: 'pause' });
    assert.deepStrictEqual(shortcutAction(key('e'), TABS), { type: 'export', format: 'json' });
    assert.deepStrictEqual(shortcutAction(key('C'), TABS), { type: 'export', format: 'csv' });
    assert.strictEqual(shortcutAction(key('x'), TABS), null);
});

test('the new keys obey the same guards as 1-5 and R (#556)', () => {
    assert.strictEqual(shortcutAction(key('s', { ctrlKey: true }), TABS), null);
    assert.strictEqual(shortcutAction(key('e', { metaKey: true }), TABS), null);
    assert.strictEqual(shortcutAction(key('p', { altKey: true }), TABS), null);
    for (const tagName of ['INPUT', 'TEXTAREA', 'SELECT']) {
        assert.strictEqual(shortcutAction({ key: 's', target: { tagName } }, TABS), null);
    }
    assert.strictEqual(shortcutAction({ key: 'c', target: { tagName: 'DIV', isContentEditable: true } }, TABS), null);
    assert.strictEqual(shortcutAction(key('p', { isComposing: true }), TABS), null);
    assert.strictEqual(shortcutAction(key('e', { defaultPrevented: true }), TABS), null);
});

test('holding S, P, E or C does not repeat the action (#556)', () => {
    for (const k of ['s', 'p', 'e', 'c']) {
        assert.strictEqual(shortcutAction(key(k, { repeat: true }), TABS), null, `auto-repeat of ${k}`);
    }
    assert.deepStrictEqual(shortcutAction(key('p'), TABS), { type: 'pause' }); // the first press still counts
});

test('S runs Start only while Start is enabled, and says why otherwise (#556)', () => {
    assert.deepStrictEqual(jobStartShortcutDecision({ disabled: false }), { run: true });
    const blocked = jobStartShortcutDecision({ disabled: true });
    assert.strictEqual(blocked.run, false);
    assert.match(blocked.message, /job API \(#147\)/);
    assert.strictEqual(jobStartShortcutDecision(null).run, false);
});

test('the README documents every shortcut and the Start gate (#556)', () => {
    assert.match(readme, /^## Keyboard shortcuts \(#556\)$/m);
    for (const k of ['`1`–`5`', '`R`', '`P`', '`S`', '`E`', '`C`']) {
        assert.ok(readme.includes(k), `README lists ${k}`);
    }
    assert.match(readme, /job API exists \(#147\)/);
});

test('app wires the shortcuts to the controls the mouse uses (#556)', () => {
    const setup = app.slice(app.indexOf('function setupShortcuts()'), app.indexOf('// #963:'));
    assert.match(setup, /if \(stageDrilldownIsOpen\(\)\) return;/);
    assert.match(setup, /jobStartShortcutDecision\(startBtn\)/);
    assert.match(setup, /startBtn\.click\(\)/);
    assert.match(setup, /getElementById\('pause-refresh'\);[\s\S]*?pauseBtn\.click\(\)/);
    assert.match(setup, /exportActivityLog\(action\.format\)/);
    assert.match(setup, /\['job-start', 'S'\], \['pause-refresh', 'P'\], \['export-activity-json', 'E'\], \['export-activity-csv', 'C'\]/);
    // Only the existing tab shortcut moves focus. The new keys leave it where it is.
    assert.strictEqual((setup.match(/\.focus\(\)/g) || []).length, 1);
});
