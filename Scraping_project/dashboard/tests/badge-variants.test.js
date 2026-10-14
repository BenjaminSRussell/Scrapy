// Run: node --test Scraping_project/dashboard/tests/
// #1024: five badge variants, and stage chips use them (unknown is not danger).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { stageBadge } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const guide = fs.readFileSync(path.join(__dirname, '..', 'styleguide.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const VARIANTS = ['info', 'success', 'warning', 'danger', 'unknown'];

test('CSS defines info, success, warning, danger, and unknown', () => {
    for (const v of VARIANTS) {
        assert.match(html, new RegExp('\\.badge-' + v + '\\s*\\{'));
        assert.match(guide, new RegExp('badge-' + v));
    }
});

test('stage badges start unknown on overview and pipeline cards', () => {
    for (let n = 1; n <= 4; n++) {
        const re = new RegExp('class="card-badge badge-unknown" data-stage-badge="' + n + '"', 'g');
        assert.strictEqual((html.match(re) || []).length, 2, 'stage ' + n);
    }
    assert.ok(!/class="card-badge badge-success">Running/.test(html));
});

test('stageBadge maps the five states and unknown wins', () => {
    assert.deepStrictEqual(stageBadge({ unknown: true, rate: 9, errors: 4 }),
        { variant: 'unknown', label: 'Unknown' });
    assert.deepStrictEqual(stageBadge({ rate: 1.5, total: 10, errors: 3 }),
        { variant: 'success', label: 'Running' });
    assert.deepStrictEqual(stageBadge({ rate: 0, total: 4, errors: 2 }),
        { variant: 'danger', label: 'Error' });
    assert.deepStrictEqual(stageBadge({ rate: 0, total: 4, errors: 0 }),
        { variant: 'warning', label: 'Standby' });
    assert.deepStrictEqual(stageBadge({ rate: 0, total: 0 }),
        { variant: 'info', label: 'Pending' });
    assert.deepStrictEqual(stageBadge({ rate: 1, threshold: 2, total: 0 }),
        { variant: 'info', label: 'Pending' });
});

test('a failed fetch paints stage badges unknown, not danger', () => {
    assert.match(app, /applyStageBadges\(rates, metrics, false\)/);
    assert.match(app, /applyStageBadges\(null, null, true\)/);
    assert.ok(!/classList\.add\('badge-danger'\)/.test(app));
    assert.match(app, /data-stage-badge/);
});
