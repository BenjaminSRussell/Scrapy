// Run: node --test Scraping_project/dashboard/tests/
// #967: kill-redis and pause-kafka are staging-only stubs, with a postmortem link.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const gameday = require('../chaos-gameday.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');
const template = fs.readFileSync(path.join(__dirname, '..', 'chaos-gameday-postmortem.txt'), 'utf8');

function button(id) {
    const m = html.match(new RegExp(`<button type="button"[^>]*id="${id}"[^>]*>`));
    assert.ok(m, id);
    return m[0];
}

function fakeDoc() {
    const nodes = {};
    function make(id) {
        return { id: id, hidden: true, disabled: true, textContent: '' };
    }
    nodes['chaos-gameday-status'] = make('chaos-gameday-status');
    nodes['chaos-gameday-result'] = make('chaos-gameday-result');
    nodes['chaos-gameday-result'].textContent = 'stale';
    nodes['chaos-btn-kill-redis'] = make('chaos-btn-kill-redis');
    nodes['chaos-btn-pause-kafka'] = make('chaos-btn-pause-kafka');
    return {
        getElementById(id) { return nodes[id] || null; },
        nodes: nodes,
    };
}

test('both stub buttons ship hidden and the postmortem template is linked', () => {
    for (const id of ['chaos-btn-kill-redis', 'chaos-btn-pause-kafka']) {
        const tag = button(id);
        assert.match(tag, / hidden/);
        assert.match(tag, / disabled/);
        assert.match(tag, /data-chaos-action="(kill-redis|pause-kafka)"/);
    }
    assert.match(html, /id="chaos-btn-kill-redis"[^>]*>Kill Redis \(stub\)/);
    assert.match(html, /id="chaos-btn-pause-kafka"[^>]*>Pause Kafka \(stub\)/);
    assert.match(html, /id="chaos-postmortem-link" href="chaos-gameday-postmortem\.txt">Postmortem template<\/a>/);
    assert.match(html, /id="chaos-gameday-status"[^>]*>Game-day fault injection is off/);
    assert.match(html, /<script src="chaos-gameday\.js"><\/script>/);
    assert.match(template, /Chaos game-day postmortem/);
    assert.match(template, /kill-redis \| pause-kafka/);
    assert.match(readme, /CC_CHAOS_GAMEDAY/);
    assert.match(readme, /CC_ENV/);
});

test('buttons stay off unless the server reports a staging stub', () => {
    assert.deepStrictEqual(gameday.panelState(null).actions, []);
    assert.strictEqual(gameday.panelState(null).enabled, false);
    assert.strictEqual(gameday.panelState({ ok: true, enabled: true, stub: true, effect: 'none', env: 'production' }).enabled, false);
    assert.strictEqual(gameday.panelState({ ok: true, enabled: true, stub: true, effect: 'none', env: 'local' }).enabled, false);
    assert.strictEqual(gameday.panelState({ ok: true, enabled: false, stub: true, effect: 'none', env: 'staging' }).enabled, false);
    assert.strictEqual(gameday.panelState({ ok: true, enabled: true, stub: false, effect: 'none', env: 'staging' }).enabled, false);
    const armed = { ok: true, enabled: true, stub: true, effect: 'none', env: 'staging' };
    assert.deepStrictEqual(gameday.panelState(armed).actions, ['kill-redis', 'pause-kafka']);
    assert.match(gameday.panelState(armed).message, /do not stop Redis or Kafka/);
});

test('applyChaosPanel reveals the stubs only for an armed staging status', () => {
    const doc = fakeDoc();
    gameday.applyChaosPanel(doc, { ok: true, enabled: false, stub: true, effect: 'none', env: 'staging' });
    assert.strictEqual(doc.nodes['chaos-btn-kill-redis'].hidden, true);
    assert.strictEqual(doc.nodes['chaos-btn-pause-kafka'].disabled, true);
    assert.strictEqual(doc.nodes['chaos-gameday-result'].textContent, '');
    gameday.applyChaosPanel(doc, { ok: true, enabled: true, stub: true, effect: 'none', env: 'staging' });
    assert.strictEqual(doc.nodes['chaos-btn-kill-redis'].hidden, false);
    assert.strictEqual(doc.nodes['chaos-btn-kill-redis'].disabled, false);
    assert.strictEqual(doc.nodes['chaos-btn-pause-kafka'].hidden, false);
    assert.match(doc.nodes['chaos-gameday-status'].textContent, /Staging game day is armed/);
});

test('the client describes a stub and refuses anything that is not one', () => {
    const kill = gameday.describeInjection({ ok: true, accepted: 'kill-redis', stub: true, effect: 'none' });
    assert.match(kill.text, /Kill Redis \(stub\)/);
    assert.match(kill.text, /No Redis or Kafka process was touched/);
    const pause = gameday.describeInjection({ ok: true, accepted: 'pause-kafka', stub: true, effect: 'none' });
    assert.match(pause.text, /Pause Kafka \(stub\)/);
    for (const body of [null, { ok: false }, { ok: true, accepted: 'kill-redis', stub: true, effect: 'stopped' },
        { ok: true, accepted: 'drop-database', stub: true, effect: 'none' }]) {
        assert.strictEqual(gameday.describeInjection(body).text, gameday.REFUSED);
    }
});

test('app.js posts the action and writes textContent, not markup', () => {
    const start = app.indexOf('function setupChaosGameday');
    assert.ok(start > 0);
    const fn = app.slice(start, app.indexOf('\nfunction initialize', start));
    assert.match(fn, /api\/chaos\/gameday/);
    assert.match(fn, /method: 'POST'/);
    assert.match(fn, /JSON\.stringify\(\{ action: action \}\)/);
    assert.match(fn, /result\.textContent = described\.text/);
    assert.ok(!fn.includes('innerHTML'));
    assert.match(app, /setupChaosGameday\(\)/);
});
