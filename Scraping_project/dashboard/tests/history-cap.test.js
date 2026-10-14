// Run: node --test Scraping_project/dashboard/tests/
// #691: client history is one ring buffer, not the chart axis max.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    HISTORY_MAX_POINTS,
    pushHistorySample,
    boundHistoryBuffer,
    assertHistoryBound,
    enforceHistoryCap,
} = require('../format-utils.js');

test('HISTORY_MAX_POINTS is the shared client cap', () => {
    assert.strictEqual(HISTORY_MAX_POINTS, 50);
});

test('pushHistorySample keeps only the last HISTORY_MAX_POINTS values', () => {
    const buf = [];
    const extra = 30;
    const n = HISTORY_MAX_POINTS + extra;
    for (let i = 0; i < n; i++) pushHistorySample(buf, i);
    assert.strictEqual(buf.length, HISTORY_MAX_POINTS);
    assert.strictEqual(buf[0], extra);
    assert.strictEqual(buf[buf.length - 1], n - 1);
    assertHistoryBound(buf.length);
});

test('each series is capped on its own when the chart label series is still short', () => {
    const series = {
        timestamps: ['now'],
        urls: Array.from({ length: HISTORY_MAX_POINTS + 12 }, (_, i) => i),
        pages: [],
        sampleTimes: Array.from({ length: HISTORY_MAX_POINTS + 4 }, (_, i) => i),
        note: 'not a series',
    };
    enforceHistoryCap(series, 'start');
    assert.deepStrictEqual(series.timestamps, ['now']);
    assert.deepStrictEqual(series.pages, []);
    assert.strictEqual(series.urls.length, HISTORY_MAX_POINTS);
    assert.strictEqual(series.urls[0], 12);
    assert.strictEqual(series.sampleTimes.length, HISTORY_MAX_POINTS);
    assert.strictEqual(series.note, 'not a series');
});

test('newest-first logs drop the tail past the shared cap', () => {
    const log = [];
    for (let i = 0; i < HISTORY_MAX_POINTS + 5; i++) {
        log.unshift(i);
        boundHistoryBuffer(log, 'end');
    }
    assert.strictEqual(log.length, HISTORY_MAX_POINTS);
    assert.strictEqual(log[0], HISTORY_MAX_POINTS + 4);
    assert.strictEqual(log[log.length - 1], 5);
});

test('debug assert rejects a length above the cap and is quiet in production', () => {
    assert.throws(() => assertHistoryBound(HISTORY_MAX_POINTS + 1), /exceeds cap/);
    const prev = process.env.NODE_ENV;
    process.env.NODE_ENV = 'production';
    try {
        assert.strictEqual(assertHistoryBound(HISTORY_MAX_POINTS + 100), true);
    } finally {
        if (prev === undefined) delete process.env.NODE_ENV;
        else process.env.NODE_ENV = prev;
    }
});

test('UI help cites the cap and app.js has no separate chart max', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const chartHelp = html.match(/id="chart-history-cap"[^>]*>[^<]+/);
    assert.ok(chartHelp, 'chart history help text missing');
    assert.match(chartHelp[0], new RegExp(`last ${HISTORY_MAX_POINTS} samples`));
    assert.match(chartHelp[0], /independent of the chart axis/);
    const activityHelp = html.match(/id="activity-log-cap"[^>]*>[^<]+/);
    assert.ok(activityHelp, 'activity log help text missing');
    assert.match(activityHelp[0], new RegExp(`last ${HISTORY_MAX_POINTS} events`));

    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    assert.match(app, /pushHistorySample\(historicalData\.timestamps/);
    assert.match(app, /pushHistorySample\(historicalData\.sampleTimes/);
    assert.match(app, /enforceHistoryCap\(historicalData, 'start'\)/);
    assert.match(app, /boundHistoryBuffer\(activityLog, 'end'\)/);
    assert.ok(!/maxDataPoints/.test(app));
    assert.match(app, /Array\.isArray\(historicalData\[key\]\)\) historicalData\[key\]\.length = 0/);
});
