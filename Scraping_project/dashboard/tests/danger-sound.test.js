// Run: node --test Scraping_project/dashboard/tests/danger-sound.test.js
// #1037: optional danger chime, off by default, persisted, silent under
// reduced-motion and a mute preference.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { parseDangerSoundEnabled, dangerSoundDecision } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('stored sound preference defaults off', () => {
    for (const v of [null, undefined, '', '0', 'false', 'off', 'no', false, 0, 'maybe']) {
        assert.strictEqual(parseDangerSoundEnabled(v), false, String(v));
    }
    for (const v of [true, 1, '1', 'true', 'TRUE', 'on', ' yes ']) {
        assert.strictEqual(parseDangerSoundEnabled(v), true, String(v));
    }
});

test('chime only for an opted-in new danger item', () => {
    assert.deepStrictEqual(dangerSoundDecision({ type: 'danger', enabled: false }), { play: false, reason: 'off' });
    assert.deepStrictEqual(dangerSoundDecision({ type: 'danger' }), { play: false, reason: 'off' });
    assert.deepStrictEqual(dangerSoundDecision({ type: 'danger', enabled: '0' }), { play: false, reason: 'off' });
    assert.deepStrictEqual(
        dangerSoundDecision({ type: 'danger', enabled: true }),
        { play: true, reason: 'danger' }
    );
    for (const type of ['success', 'warning', 'info', 'timeout', '']) {
        assert.strictEqual(dangerSoundDecision({ type, enabled: true }).play, false, type);
        assert.strictEqual(dangerSoundDecision({ type, enabled: true }).reason, 'not-danger');
    }
});

test('reduced motion and prefers-muted silence an enabled toggle', () => {
    const on = { type: 'danger', enabled: '1', announced: true };
    assert.deepStrictEqual(
        dangerSoundDecision({ ...on, reducedMotion: true }),
        { play: false, reason: 'reduced-motion' }
    );
    assert.deepStrictEqual(
        dangerSoundDecision({ ...on, prefersMuted: true }),
        { play: false, reason: 'prefers-muted' }
    );
    assert.deepStrictEqual(
        dangerSoundDecision({ ...on, announced: false }),
        { play: false, reason: 'repeat' }
    );
    assert.strictEqual(dangerSoundDecision({ ...on, reducedMotion: false, prefersMuted: false }).play, true);
});

test('markup ships the toggle unchecked with an accessible name', () => {
    const input = html.match(/<input type="checkbox" id="danger-sound-toggle"[^>]*>/);
    assert.ok(input, 'danger-sound checkbox present');
    assert.ok(!/\schecked\b/.test(input[0]), 'not checked in markup');
    assert.match(html, /<label class="danger-sound-label" for="danger-sound-toggle">[\s\S]*Sound on danger/);
    assert.match(html, /id="danger-sound-hint" class="visually-hidden"/);
    assert.match(html, /aria-describedby="danger-sound-hint"/);
});

test('app persists the toggle and gates the chime on the announcer', () => {
    assert.match(app, /STORE_DANGER_SOUND_KEY = 'cc\.dangerSound'/);
    assert.match(app, /parseDangerSoundEnabled\(storeGet\(STORE_DANGER_SOUND_KEY\)\)/);
    assert.match(app, /storeSet\(STORE_DANGER_SOUND_KEY, dangerSoundEnabled \? '1' : '0'\)/);
    assert.match(app, /prefers-reduced-motion: reduce/);
    assert.match(app, /prefers-muted/);
    const fn = app.slice(app.indexOf('function announceActivity'), app.indexOf('let activityPinned'));
    assert.match(fn, /considerDangerSound\(type, text != null\)/);
    assert.match(fn, /node\.textContent = text/);
    assert.ok(!/innerHTML/.test(fn));
    assert.match(app, /setupDangerSound\(\)/);
});
