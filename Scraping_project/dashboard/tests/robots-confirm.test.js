// Stage 1 ignore_robots dual confirm UI (#938).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { dualConfirmReady, CONFIRM_PHRASE } = require('../robots-confirm.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'robots-confirm.html'), 'utf8');

test('defaults are off and the phrase is required before submit', () => {
    assert.strictEqual(CONFIRM_PHRASE, 'IGNORE ROBOTS');
    assert.strictEqual(dualConfirmReady({}), false);
    assert.strictEqual(dualConfirmReady({
        rustmapper_ignore_robots: true,
        stage1_confirm: false,
        reason: 'owned',
        typed: CONFIRM_PHRASE,
    }), false);
    assert.strictEqual(dualConfirmReady({
        rustmapper_ignore_robots: false,
        stage1_confirm: true,
        reason: 'owned',
        typed: CONFIRM_PHRASE,
    }), false);
    assert.strictEqual(dualConfirmReady({
        rustmapper_ignore_robots: true,
        stage1_confirm: true,
        reason: '   ',
        typed: CONFIRM_PHRASE,
    }), false);
    assert.strictEqual(dualConfirmReady({
        rustmapper_ignore_robots: true,
        stage1_confirm: true,
        reason: 'owned lab host',
        typed: 'yes',
    }), false);
    assert.strictEqual(dualConfirmReady({
        rustmapper_ignore_robots: true,
        stage1_confirm: true,
        reason: 'owned lab host',
        typed: CONFIRM_PHRASE,
    }), true);
});

test('the page ships both confirms unchecked and posts to the audit endpoint', () => {
    assert.match(html, /id="rustmapper-ignore" type="checkbox"/);
    assert.match(html, /id="stage1-confirm" type="checkbox"/);
    assert.doesNotMatch(html, /id="rustmapper-ignore"[^>]*checked/);
    assert.doesNotMatch(html, /id="stage1-confirm"[^>]*checked/);
    assert.match(html, /id="robots-submit"[^>]*disabled/);
    assert.match(html, /src="robots-confirm.js"/);
    assert.match(fs.readFileSync(path.join(__dirname, '..', 'robots-confirm.js'), 'utf8'), /\/api\/stage1\/ignore-robots/);
    assert.doesNotMatch(html, /<script(?![^>]*src=)/);
});
