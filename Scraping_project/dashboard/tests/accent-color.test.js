'use strict';
// #1017: accent picker writes CSS variables and is remembered.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    DEFAULT_ACCENT, parseAccentColor, accentInk, accentBorder, accentCssVars,
} = require('../format-utils.js');

test('parseAccentColor accepts only #rrggbb and falls back otherwise', () => {
    assert.strictEqual(DEFAULT_ACCENT, '#2563eb');
    assert.strictEqual(parseAccentColor('#2563EB'), '#2563eb');
    assert.strictEqual(parseAccentColor('  #abcdef  '), '#abcdef');
    for (const bad of ['red', '#fff', '#2563eb;', 'javascript:alert(1)', '', null, undefined, '#2563eb;color:red']) {
        assert.strictEqual(parseAccentColor(bad), DEFAULT_ACCENT, String(bad));
    }
    assert.strictEqual(parseAccentColor('nope', null), null);
});

test('accent ink picks the higher-contrast of white and near-black', () => {
    assert.strictEqual(accentInk('#2563eb'), '#ffffff');
    assert.strictEqual(accentInk('#000000'), '#ffffff');
    assert.strictEqual(accentInk('#ffffff'), '#111827');
    assert.strictEqual(accentInk('#fde68a'), '#111827');
});

test('accent border stays put when contrast is enough and darkens when it is not', () => {
    assert.strictEqual(accentBorder('#2563eb'), '#2563eb');
    assert.strictEqual(accentBorder('#7c3aed'), '#7c3aed');
    const pale = accentBorder('#ffffff');
    assert.notStrictEqual(pale, '#ffffff');
    assert.match(pale, /^#[0-9a-f]{6}$/);
    const yellow = accentBorder('#ffff00');
    assert.match(yellow, /^#[0-9a-f]{6}$/);
});

test('accentCssVars writes the three custom properties', () => {
    const vars = accentCssVars('#2563eb');
    assert.deepStrictEqual(Object.keys(vars).sort(), ['--accent', '--accent-border', '--accent-ink']);
    assert.strictEqual(vars['--accent'], '#2563eb');
    assert.strictEqual(vars['--accent-ink'], '#ffffff');
    assert.strictEqual(vars['--accent-border'], '#2563eb');
    const light = accentCssVars('#ffffff');
    assert.strictEqual(light['--accent'], '#ffffff');
    assert.strictEqual(light['--accent-ink'], '#111827');
    assert.notStrictEqual(light['--accent-border'], '#ffffff');
});

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('stylesheet wires buttons and the active tab border to the accent variables', () => {
    assert.match(html, /--accent:\s*#2563eb/);
    assert.match(html, /--accent-ink:\s*#ffffff/);
    assert.match(html, /--accent-border:\s*#2563eb/);
    assert.match(html, /\.btn\s*\{[^}]*background:\s*var\(--accent\)/);
    assert.match(html, /\.btn\s*\{[^}]*color:\s*var\(--accent-ink\)/);
    assert.match(html, /\.tab-button\.active\s*\{[^}]*border-bottom-color:\s*var\(--accent-border\)/);
    assert.match(html, /<input type="color" id="accent-color"/);
    assert.match(html, /id="accent-reset"[^>]*aria-label="Reset accent color to default"/);
});

test('app applies the stored accent and persists picker changes (#1017)', () => {
    assert.match(app, /STORE_ACCENT_KEY = 'cc\.accent'/);
    assert.match(app, /root\.style\.setProperty\(name, vars\[name\]\)/);
    assert.match(app, /storeSet\(STORE_ACCENT_KEY, next\)/);
    assert.match(app, /storeDel\(STORE_ACCENT_KEY\)/);
    assert.match(app, /setupAccentControl\(\)/);
    assert.match(app, /accentCssVars\(hex\)/);
});
