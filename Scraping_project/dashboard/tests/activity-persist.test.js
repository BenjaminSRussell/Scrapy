// Run: node --test Scraping_project/dashboard/tests/
// #1029: the activity log is a sessionStorage ring (cap 50). A reload restores
// recent entries and shows a stale note. Closing the tab drops them.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { ACTIVITY_LOG_CAP, capActivityLog, activityStaleNote, restoreActivityLog } = require('../format-utils.js');

function item(i, extra) {
    return Object.assign({ type: 'success', message: `event ${i}`, timestamp: `t${i}` }, extra);
}

test('cap is 50 and keeps the newest entries (newest first)', () => {
    assert.strictEqual(ACTIVITY_LOG_CAP, 50);
    const items = [];
    for (let i = 0; i < 60; i++) items.push(item(i));
    const capped = capActivityLog(items);
    assert.strictEqual(capped.length, 50);
    assert.strictEqual(capped[0].message, 'event 0');
    assert.strictEqual(capped[49].message, 'event 49');
    assert.ok(!capped.some((row) => row.message === 'event 50'));
});

test('blank messages and junk rows are dropped; known types stay', () => {
    const capped = capActivityLog([
        { type: 'warning', message: '  keep  ', timestamp: '1:00' },
        { type: 'nope!', message: 'bad type becomes info', timestamp: 5 },
        { type: 'danger', message: '   ', timestamp: 'x' },
        null,
        'nope',
        { type: 'timeout', message: 'timed out', timestamp: '2:00' },
        { message: 'missing type' },
    ]);
    assert.deepStrictEqual(capped, [
        { type: 'warning', message: '  keep  ', timestamp: '1:00' },
        { type: 'info', message: 'bad type becomes info', timestamp: '' },
        { type: 'timeout', message: 'timed out', timestamp: '2:00' },
        { type: 'info', message: 'missing type', timestamp: '' },
    ]);
});

test('only type, message and timestamp are stored', () => {
    const capped = capActivityLog([
        { type: 'success', message: 'hi', timestamp: 'now', restored: true, html: '<script>' },
    ]);
    assert.deepStrictEqual(capped, [{ type: 'success', message: 'hi', timestamp: 'now' }]);
});

test('restore round-trips a ring and attaches a stale note', () => {
    const raw = JSON.stringify([item(1), item(2)]);
    const restored = restoreActivityLog(raw);
    assert.strictEqual(restored.items.length, 2);
    assert.strictEqual(restored.items[0].message, 'event 1');
    assert.strictEqual(restored.staleNote, activityStaleNote(2));
    assert.match(restored.staleNote, /before the reload/);
    assert.match(restored.staleNote, /may be stale/);
    assert.match(restored.staleNote, /2 earlier entries/);
});

test('one restored entry uses the singular note', () => {
    const restored = restoreActivityLog(JSON.stringify([item(1)]));
    assert.match(restored.staleNote, /1 earlier entry/);
    assert.match(restored.staleNote, /may be stale/);
    assert.strictEqual(activityStaleNote(0), null);
    assert.strictEqual(activityStaleNote(-3), null);
    assert.strictEqual(activityStaleNote('nope'), null);
});

test('corrupt, empty and non-array payloads restore nothing', () => {
    for (const raw of [null, undefined, '', '{', '{"a":1}', '[]', 'null', '"hi"', '   ']) {
        const restored = restoreActivityLog(raw);
        assert.deepStrictEqual(restored.items, [], String(raw));
        assert.strictEqual(restored.staleNote, null, String(raw));
    }
});

test('a stored ring larger than the cap is trimmed before the note', () => {
    const items = [];
    for (let i = 0; i < 80; i++) items.push(item(i));
    const restored = restoreActivityLog(JSON.stringify(items));
    assert.strictEqual(restored.items.length, 50);
    assert.match(restored.staleNote, /50 earlier entries/);
});

test('huge messages are truncated and a huge blob is ignored', () => {
    const long = 'x'.repeat(5000);
    const capped = capActivityLog([{ type: 'danger', message: long, timestamp: 't' }]);
    assert.strictEqual(capped[0].message.length, 1000);
    const huge = '[' + '"x",'.repeat(200000) + '"y"]';
    assert.ok(huge.length > 200000);
    assert.deepStrictEqual(restoreActivityLog(huge).items, []);
});

test('app.js restores sessionStorage before the fresh init line and clear wipes it (#1029)', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(src, /const ACTIVITY_STORE_KEY = 'cc\.activityLog'/);
    assert.match(src, /window\.sessionStorage/);
    assert.match(src, /capActivityLog\(activityLog\)/);
    assert.match(src, /restoreActivityLog\(sessionGet\(ACTIVITY_STORE_KEY\)\)/);
    assert.match(src, /activityLog\.length > ACTIVITY_LOG_CAP/);
    const init = src.slice(src.indexOf('function initialize'), src.indexOf("console.log('Dashboard ready!')"));
    const restoreAt = init.indexOf('restorePersistedActivityLog()');
    const freshAt = init.indexOf("formatActivity('success', 'Pipeline Control Center initialized')");
    assert.ok(restoreAt >= 0 && freshAt > restoreAt, 'restore runs before the new init entry');
    assert.ok(init.indexOf('addActivityLogItem(line.type, line.message)', freshAt) > freshAt);
    const clear = src.slice(src.indexOf('function clearActivityLog'), src.indexOf('function detectSignificantChanges'));
    assert.match(clear, /activityLog\.length = 0/);
    assert.match(clear, /persistActivityLog\(\)/);
    assert.match(clear, /activityStaleText = null/);
    assert.ok(!src.includes('activity-log.innerHTML'), 'clear does not use innerHTML');
    const restoreFn = src.slice(src.indexOf('function restorePersistedActivityLog'), src.indexOf('function clearActivityLog'));
    assert.ok(!/announceActivity\(/.test(restoreFn), 'restored rows are not announced one by one');
    assert.match(restoreFn, /restored: true/);
});

test('stale note is styled and not a static seed in the feeds (#1029)', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    assert.match(html, /\.activity-stale-note\s*\{/);
    assert.match(html, /\.activity-restored-tag\s*\{/);
    assert.match(html, /\.activity-item\.restored\s*\{/);
    const forced = html.slice(html.indexOf('@media (forced-colors: active)'));
    assert.match(forced, /\.activity-stale-note\s*\{[^}]*border:\s*1px dashed CanvasText/);
    for (const id of ['overview-activity', 'activity-log']) {
        const m = html.match(new RegExp(`<div[^>]*id="${id}"[^>]*>([\\s\\S]*?)</div>`));
        assert.strictEqual(m[1].trim(), '', `${id} stays empty in HTML`);
    }
});
