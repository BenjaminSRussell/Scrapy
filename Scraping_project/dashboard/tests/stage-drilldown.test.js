// Run: node --test Scraping_project/dashboard/tests/
// Stage cards open a side panel (#1033): keyboard activate, Escape closes,
// panel shows a metric subset and placeholder job/queue links.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { stageDrilldown, stageDrilldownKey } = require('../format-utils.js');

test('stageDrilldownKey activates on Enter and Space and closes on Escape', () => {
    assert.strictEqual(stageDrilldownKey('Enter'), 'activate');
    assert.strictEqual(stageDrilldownKey(' '), 'activate');
    assert.strictEqual(stageDrilldownKey('Spacebar'), 'activate');
    assert.strictEqual(stageDrilldownKey('Escape'), 'close');
    assert.strictEqual(stageDrilldownKey('Esc'), 'close');
    assert.strictEqual(stageDrilldownKey('r'), null);
    assert.strictEqual(stageDrilldownKey('Tab'), null);
});

test('each stage exposes a metric subset and placeholder links', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    for (const stage of [1, 2, 3, 4]) {
        const spec = stageDrilldown(stage);
        assert.ok(spec, `stage ${stage}`);
        assert.strictEqual(spec.stage, stage);
        assert.ok(spec.title);
        assert.ok(spec.metrics.length >= 2 && spec.metrics.length < 4, 'subset of the card');
        for (const metric of spec.metrics) {
            assert.match(html, new RegExp(`id="${metric.id}"`));
        }
        assert.strictEqual(spec.links.length, 2);
        const kinds = spec.links.map(link => link.kind).sort();
        assert.deepStrictEqual(kinds, ['jobs', 'queue']);
        for (const link of spec.links) {
            assert.strictEqual(link.placeholder, true);
            assert.match(link.href, /^#/);
            assert.match(link.label, /job|queue/i);
        }
    }
    assert.strictEqual(stageDrilldown(0), null);
    assert.strictEqual(stageDrilldown('nope'), null);
    assert.strictEqual(stageDrilldown('2').title, 'Page Analysis');
});

test('stage cards are keyboard buttons and the dialog lives outside the tab panels', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    const cards = [...html.matchAll(/<div class="stage-card"[^>]*>/g)].map(m => m[0]);
    assert.strictEqual(cards.length, 4);
    cards.forEach((card, i) => {
        assert.match(card, /role="button"/);
        assert.match(card, /tabindex="0"/);
        assert.match(card, new RegExp(`data-stage="${i + 1}"`));
        assert.match(card, /aria-controls="stage-drilldown"/);
        assert.match(card, /aria-expanded="false"/);
        assert.match(card, /aria-haspopup="dialog"/);
    });
    assert.match(html, /id="stage-drilldown"[^>]*hidden/);
    assert.match(html, /role="dialog"/);
    assert.match(html, /id="stage-drilldown-close"/);
    assert.match(html, /id="stage-drilldown-links"/);
    assert.match(html, /id="stage-drilldown-metrics"/);
    assert.match(html, /id="stage-drilldown-announce"/);
    const panelAt = html.indexOf('id="stage-drilldown"');
    const pipelineEnd = html.indexOf('id="tab-performance"');
    assert.ok(panelAt > pipelineEnd, 'panel is not inside a hidden tab panel');
    assert.match(html, /\.stage-card:focus-visible/);
    assert.match(html, /\.stage-drilldown\[hidden\]\s*\{[^}]*display:\s*none/);
    assert.match(app, /setupStageDrilldown\(\)/);
    assert.match(app, /stageDrilldownKey\(event\.key\) !== 'activate'/);
    assert.match(app, /stageDrilldownKey\(event\.key\) === 'close'/);
    assert.match(app, /syncStageDrilldownValues\(\)/);
    assert.match(app, /a\[data-placeholder="true"\]/);
    assert.match(app, /if \(stageDrilldownIsOpen\(\)\) return/);
});
