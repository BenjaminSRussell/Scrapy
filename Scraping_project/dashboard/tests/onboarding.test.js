'use strict';
// #1021: first-visit coach marks. Shown once; Skip (and Done / Finish) persist
// localStorage key onboardingDone. Checklist: Overview, System Health, Grafana.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    ONBOARDING_STORAGE_KEY, ONBOARDING_STEPS, onboardingShouldShow, onboardingReduce,
} = require('../format-utils.js');

test('storage key is onboardingDone and the three steps are Overview, System Health, Grafana', () => {
    assert.strictEqual(ONBOARDING_STORAGE_KEY, 'onboardingDone');
    assert.deepStrictEqual(ONBOARDING_STEPS.map(s => s.id), ['overview', 'system', 'grafana']);
    assert.deepStrictEqual(ONBOARDING_STEPS.map(s => s.title), ['Overview', 'System Health', 'Grafana']);
    assert.deepStrictEqual(ONBOARDING_STEPS.map(s => s.targetId), ['tabbtn-overview', 'tabbtn-system', 'grafana-link']);
});

test('tour shows once: missing or falsey storage shows it, any other value hides it', () => {
    for (const stored of [null, undefined, '', '   ', '0', 'false', 'False']) {
        assert.strictEqual(onboardingShouldShow(stored), true, String(stored));
    }
    for (const stored of ['1', 'true', 'done', 'yes']) {
        assert.strictEqual(onboardingShouldShow(stored), false, String(stored));
    }
});

test('Skip and Done dismiss and persist from any step', () => {
    for (const type of ['skip', 'done']) {
        const view = onboardingReduce({ index: 1 }, { type });
        assert.strictEqual(view.show, false);
        assert.strictEqual(view.persist, true);
        assert.strictEqual(view.targetId, null);
    }
    const first = onboardingReduce({ index: 0 }, { type: 'skip' });
    assert.strictEqual(first.show, false);
    assert.strictEqual(first.persist, true);
});

test('Next walks the coach marks and Finish on the last step persists', () => {
    const overview = onboardingReduce({ index: 0 }, { type: 'show' });
    assert.strictEqual(overview.show, true);
    assert.strictEqual(overview.persist, false);
    assert.strictEqual(overview.stepId, 'overview');
    assert.strictEqual(overview.targetId, 'tabbtn-overview');
    assert.strictEqual(overview.tab, 'overview');

    const system = onboardingReduce(overview, { type: 'next' });
    assert.strictEqual(system.stepId, 'system');
    assert.strictEqual(system.targetId, 'tabbtn-system');
    assert.strictEqual(system.tab, 'system');
    assert.strictEqual(system.persist, false);

    const grafana = onboardingReduce(system, { type: 'next' });
    assert.strictEqual(grafana.stepId, 'grafana');
    assert.strictEqual(grafana.targetId, 'grafana-link');
    assert.strictEqual(grafana.tab, null);
    assert.strictEqual(grafana.show, true);

    const finished = onboardingReduce(grafana, { type: 'next' });
    assert.strictEqual(finished.show, false);
    assert.strictEqual(finished.persist, true);
});

test('Back stops at the first step; goto jumps and ignores unknown ids', () => {
    const stayed = onboardingReduce({ index: 0 }, { type: 'prev' });
    assert.strictEqual(stayed.index, 0);
    assert.strictEqual(stayed.stepId, 'overview');
    const jumped = onboardingReduce({ index: 0 }, { type: 'goto', id: 'grafana' });
    assert.strictEqual(jumped.stepId, 'grafana');
    assert.strictEqual(jumped.persist, false);
    const unknown = onboardingReduce({ index: 1 }, { type: 'goto', id: 'nope' });
    assert.strictEqual(unknown.stepId, 'system');
});

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('page has the checklist, Skip, and a persistent Grafana link', () => {
    const start = html.indexOf('id="onboarding"');
    assert.ok(start > 0);
    const section = html.slice(start, start + 1800);
    assert.match(section, /Overview/);
    assert.match(section, /System Health/);
    assert.match(section, /Grafana link/);
    assert.match(section, /id="onboarding-skip"/);
    assert.match(section, /id="onboarding-done"/);
    assert.match(section, /data-onboarding-step="overview"/);
    assert.match(section, /data-onboarding-step="system"/);
    assert.match(section, /data-onboarding-step="grafana"/);
    assert.match(section, /hidden/);
    const link = html.match(/<a\b[^>]*id="grafana-link"[^>]*>/);
    assert.ok(link, 'grafana link present');
    assert.match(link[0], /href="https?:\/\//);
    assert.match(link[0], /rel="noopener noreferrer"/);
    assert.match(link[0], /target="_blank"/);
});

test('app shows the tour from storage and wires Skip', () => {
    assert.match(app, /function setupOnboarding\(\)/);
    assert.match(app, /setupOnboarding\(\);/);
    assert.match(app, /onboardingShouldShow\(storeGet\(ONBOARDING_STORAGE_KEY\)\)/);
    assert.match(app, /storeSet\(ONBOARDING_STORAGE_KEY, '1'\)/);
    assert.match(app, /type: 'skip'/);
    assert.match(app, /is-coach-target/);
});
