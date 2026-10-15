// Run: node --test Scraping_project/dashboard/tests/
// #1013: time to first meaningful metrics. Local warm-cache target is under 2s.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    TTFM_MARK, TTFM_TARGET_MS, timeToFirstMeaningful, scheduleFirstMeaningfulMark,
} = require('../format-utils.js');

test('TTFM budget is under 2s and only a finite non-negative duration counts (#1013)', () => {
    assert.strictEqual(TTFM_MARK, 'cc:ttfm');
    assert.strictEqual(TTFM_TARGET_MS, 2000);
    const under = timeToFirstMeaningful(0);
    assert.deepStrictEqual(under, { name: 'cc:ttfm', durationMs: 0, targetMs: 2000, withinTarget: true });
    assert.strictEqual(timeToFirstMeaningful(1999).withinTarget, true);
    assert.strictEqual(timeToFirstMeaningful(1999.9).withinTarget, true);
    const over = timeToFirstMeaningful(2000);
    assert.strictEqual(over.withinTarget, false);
    assert.strictEqual(over.durationMs, 2000);
    assert.strictEqual(timeToFirstMeaningful(2500).withinTarget, false);
    for (const bad of [-1, -0.1, NaN, Infinity, -Infinity, 'fast', null, undefined]) {
        assert.strictEqual(timeToFirstMeaningful(bad), null);
    }
});

test('first meaningful mark runs once, after two frames (#1013)', () => {
    const frames = [];
    let painted = 0;
    const raf = (fn) => { frames.push(fn); return frames.length; };
    assert.strictEqual(scheduleFirstMeaningfulMark({
        already: true, requestAnimationFrame: raf, onPaint() { painted += 1; },
    }), false);
    assert.strictEqual(frames.length, 0);
    assert.strictEqual(painted, 0);

    assert.strictEqual(scheduleFirstMeaningfulMark({
        requestAnimationFrame: raf, onPaint() { painted += 1; },
    }), true);
    assert.strictEqual(painted, 0);
    assert.strictEqual(frames.length, 1);
    frames[0]();
    assert.strictEqual(painted, 0);
    assert.strictEqual(frames.length, 2);
    frames[1]();
    assert.strictEqual(painted, 1);

    let sync = 0;
    assert.strictEqual(scheduleFirstMeaningfulMark({ onPaint() { sync += 1; } }), true);
    assert.strictEqual(sync, 1);
    assert.strictEqual(scheduleFirstMeaningfulMark({}), false);
});

test('app marks TTFM after the first successful metrics paint, and the target is documented (#1013)', () => {
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');
    assert.match(app, /noteFirstMeaningfulMetrics\(\)/);
    assert.match(app, /scheduleFirstMeaningfulMark\(\{/);
    assert.match(app, /window\.__CC_TTFM__ = sample/);
    assert.match(app, /notePollOutcome\(true\);\s*noteFirstMeaningfulMetrics\(\)/);
    assert.match(readme, /under 2 seconds \(2000ms\) on a warm cache/i);
    assert.match(readme, /cc:ttfm/);
    assert.match(readme, /window\.__CC_TTFM__/);
});
