'use strict';
// #1016: compact/comfortable card density, toggled from the topbar and persisted.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { parseDensity, nextDensity } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('parseDensity keeps only compact; nextDensity flips the two modes', () => {
    assert.strictEqual(parseDensity('compact'), 'compact');
    assert.strictEqual(parseDensity(' COMPACT '), 'compact');
    assert.strictEqual(parseDensity('comfortable'), 'comfortable');
    assert.strictEqual(parseDensity(null), 'comfortable');
    assert.strictEqual(parseDensity('dense'), 'comfortable');
    assert.strictEqual(nextDensity('comfortable'), 'compact');
    assert.strictEqual(nextDensity('compact'), 'comfortable');
    assert.strictEqual(nextDensity('nope'), 'compact');
});

test('topbar toggle persists and compact cuts card padding 30% (#1016)', () => {
    const topbar = html.slice(html.indexOf('<header class="topbar">'), html.indexOf('</header>'));
    assert.match(topbar, /id="density-toggle"/);
    assert.match(topbar, /aria-pressed="false"/);
    assert.match(topbar, />Comfortable</);
    const rule = html.match(/html\.compact \.card,\s*html\.compact \.stage-card\s*\{([^}]*)\}/);
    assert.ok(rule, 'compact card rule present');
    assert.match(rule[1], /padding:\s*calc\(1\.5rem \* 0\.7\)/);
    assert.match(html, /\.card\s*\{[^}]*padding:\s*1\.5rem/);
    assert.match(html, /\.stage-card\s*\{[^}]*padding:\s*1\.5rem/);
    assert.match(html, /html\.compact \.card\.card-toolbar\s*\{[^}]*padding:\s*calc\(0\.75rem \* 0\.7\) calc\(1rem \* 0\.7\)/);
    assert.match(app, /const STORE_DENSITY_KEY = 'cc\.density'/);
    assert.match(app, /storeGet\(STORE_DENSITY_KEY\)/);
    assert.match(app, /storeSet\(STORE_DENSITY_KEY, next\)/);
    assert.match(app, /classList\.toggle\('compact', next === 'compact'\)/);
    assert.match(app, /setupDensityControl\(\)/);
});
