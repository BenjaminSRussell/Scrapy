// Run: node --test Scraping_project/dashboard/tests/
// #971: overview and the Activity tab are two windows on one log.
// Overview is the last 5 events, unfiltered. Chips filter the Activity tab only.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    OVERVIEW_ACTIVITY_LIMIT,
    HISTORY_MAX_POINTS,
    activityFeedPlan,
    overviewActivityCaption,
    filterActivityItems,
} = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('overview window is the last 5 of the shared log (#971)', () => {
    assert.strictEqual(OVERVIEW_ACTIVITY_LIMIT, 5);
    assert.ok(OVERVIEW_ACTIVITY_LIMIT < HISTORY_MAX_POINTS);
    const idle = activityFeedPlan(null);
    assert.deepStrictEqual(idle.map(view => view.id), ['overview-activity', 'activity-log']);
    assert.strictEqual(idle[0].maxItems, 5);
    assert.strictEqual(idle[0].levels, null);
    assert.strictEqual(idle[1].maxItems, HISTORY_MAX_POINTS);
    assert.strictEqual(idle[1].levels, null);
    const danger = activityFeedPlan(new Set(['success', 'danger']));
    assert.strictEqual(danger[0].levels, null, 'overview stays unfiltered');
    assert.strictEqual(danger[0].maxItems, 5);
    assert.deepStrictEqual(danger[1].levels, ['success', 'danger']);
    const sample = [
        { type: 'success', message: 'a' },
        { type: 'danger', message: 'b' },
        { type: 'info', message: 'c' },
        { type: 'warning', message: 'd' },
        { type: 'success', message: 'e' },
        { type: 'danger', message: 'f' },
    ];
    assert.strictEqual(sample.slice(0, danger[0].maxItems).length, 5);
    assert.deepStrictEqual(
        filterActivityItems(sample, danger[1].levels).map(item => item.message),
        ['a', 'b', 'e', 'f']
    );
});

test('a tiny cap cannot make overview longer than the ring', () => {
    const plan = activityFeedPlan(new Set(['warning']), 3);
    assert.strictEqual(plan[0].maxItems, 3);
    assert.strictEqual(plan[1].maxItems, 3);
    assert.deepStrictEqual(plan[1].levels, ['warning']);
});

test('caption names the overview limit and points filters at the Activity tab', () => {
    const caption = overviewActivityCaption();
    assert.strictEqual(caption, overviewActivityCaption(OVERVIEW_ACTIVITY_LIMIT));
    assert.match(caption, /Last 5 events/);
    assert.match(caption, /Activity tab/);
    assert.match(html, new RegExp(caption.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    const overview = html.slice(html.indexOf('id="overview-activity"'), html.indexOf('id="overview-activity"') + 400);
    assert.ok(!overview.includes('activity-chip'));
});

test('app.js has one writer and paints both feeds from activityFeedPlan', () => {
    assert.strictEqual((app.match(/activityLog\.unshift/g) || []).length, 1);
    const writer = app.slice(app.indexOf('function addActivityLogItem'), app.indexOf('function announceActivity'));
    assert.match(writer, /activityLog\.unshift/);
    assert.match(app, /activityFeedPlan\(activityLevelFilter\)/);
    assert.match(app, /activityFeedPlan\(null\)/);
    assert.match(app, /container\.id === 'activity-log' \? activityLevelFilter : null/);
    assert.ok(!/overview-activity' \? 8/.test(app), 'overview no longer hardcodes a window of 8');
});
