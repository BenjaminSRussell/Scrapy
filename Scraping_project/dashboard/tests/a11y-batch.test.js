// Run: node --test Scraping_project/dashboard/tests/
// Control Center a11y/UX batch: #153 #155 #332 #340 #341 #343 #349 #351 #352 #362.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { tabKeyTarget, staleState } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));
const body = html.slice(html.indexOf('<body'));

function mediaBlock(query) {
    const start = css.indexOf(`@media (${query})`);
    if (start < 0) return null;
    let depth = 0;
    for (let i = css.indexOf('{', start); i < css.length; i++) {
        if (css[i] === '{') depth++;
        else if (css[i] === '}' && --depth === 0) return css.slice(start, i + 1);
    }
    return null;
}

// --- #153 tabs -----------------------------------------------------------
test('tabKeyTarget implements the WAI-ARIA tabs keyboard model', () => {
    assert.strictEqual(tabKeyTarget('ArrowRight', 0, 5), 1);
    assert.strictEqual(tabKeyTarget('ArrowRight', 4, 5), 0, 'wraps forward');
    assert.strictEqual(tabKeyTarget('ArrowLeft', 0, 5), 4, 'wraps backward');
    assert.strictEqual(tabKeyTarget('Home', 3, 5), 0);
    assert.strictEqual(tabKeyTarget('End', 1, 5), 4);
    assert.strictEqual(tabKeyTarget('Enter', 1, 5), null);
    assert.strictEqual(tabKeyTarget('ArrowRight', 0, 0), null);
});

test('tablist / tab / tabpanel roles are wired with ids', () => {
    assert.match(body, /role="tablist"/);
    const tabs = [...body.matchAll(/<button[^>]*class="tab-button[^"]*"[^>]*>/g)].map(m => m[0]);
    assert.strictEqual(tabs.length, 5);
    for (const t of tabs) {
        const name = t.match(/data-tab="([a-z]+)"/)[1];
        assert.match(t, /role="tab"/);
        assert.match(t, new RegExp(`id="tabbtn-${name}"`));
        assert.match(t, new RegExp(`aria-controls="tab-${name}"`));
        assert.match(t, /aria-selected="(true|false)"/);
        assert.match(body, new RegExp(`id="tab-${name}" role="tabpanel" aria-labelledby="tabbtn-${name}"`));
    }
    assert.strictEqual(tabs.filter(t => /aria-selected="true"/.test(t)).length, 1);
    assert.strictEqual(tabs.filter(t => /tabindex="0"/.test(t)).length, 1, 'roving tabindex');
});

test('tab emoji are decorative', () => {
    for (const t of body.matchAll(/<button[^>]*role="tab"[^>]*>(.*?)<\/button>/g)) {
        assert.match(t[1], /^<span aria-hidden="true">[^<]+<\/span> \w/);
    }
});

test('app.js handles arrow keys on the tablist and keeps tabindex roving', () => {
    assert.match(app, /tabKeyTarget\(event\.key/);
    assert.match(app, /setAttribute\('tabindex', on \? '0' : '-1'\)/);
});

// --- #332 landmarks & headings -------------------------------------------
test('header / nav / main landmarks and h1 -> h2 outline', () => {
    assert.match(body, /<header class="topbar">/);
    assert.match(body, /<nav class="tab-buttons-wrap" aria-label="Dashboard sections">/);
    assert.match(body, /<main class="container" id="main">/);
    assert.strictEqual((body.match(/<h1\b/g) || []).length, 1);
    assert.ok((body.match(/<h2 class="card-title"/g) || []).length >= 15);
    assert.ok(!/<span class="card-title"/.test(body), 'card titles are headings');
    assert.ok(!/<h[3-6]\b/.test(body.slice(0, body.indexOf('<h2'))), 'no skipped levels before first h2');
});

// --- #340 contrast -------------------------------------------------------
function lum(hex) {
    const c = hex.replace('#', '').match(/../g).map(h => parseInt(h, 16) / 255)
        .map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function ratio(a, b) { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); }

test('muted text and topbar text meet WCAG AA', () => {
    const muted = css.match(/--muted-text:\s*(#[0-9a-fA-F]{6})/)[1];
    for (const bg of ['#ffffff', '#f3f4f6', '#f9fafb']) assert.ok(ratio(muted, bg) >= 4.5, `${muted} on ${bg}`);
    for (const end of ['#2563eb', '#7c3aed']) assert.ok(ratio('#ffffff', end) >= 4.5, `white on ${end}`);
    assert.ok(!/color:\s*#94a3b8/.test(css), 'no #94a3b8 text (2.6:1)');
    const rule = (sel) => (css.match(new RegExp(sel.replace(/[.]/g, '\\.') + '\\s*\\{([^}]*)\\}')) || [])[1] || '';
    assert.ok(!/opacity/.test(rule('.topbar-title p')), 'topbar subtitle not translucent');
    assert.ok(!/opacity/.test(rule('.topbar-stat-label')), 'topbar labels not translucent');
});

// --- #343 focus ----------------------------------------------------------
test('focus-visible rings exist outside forced-colors mode', () => {
    const forced = mediaBlock('forced-colors: active') || '';
    const outside = css.replace(forced, '');
    assert.match(outside, /\.tab-button:focus-visible\s*\{[^}]*outline:\s*3px solid/);
    assert.match(outside, /\.btn:focus-visible,[\s\S]*?outline:\s*3px solid/);
    assert.ok(!/:focus\s*\{[^}]*outline:\s*(none|0)/.test(css));
});

// --- #341 / #155 motion & mobile -----------------------------------------
test('prefers-reduced-motion disables hover lift, pulse, flash and shimmer', () => {
    const block = mediaBlock('prefers-reduced-motion: reduce');
    assert.ok(block, 'reduced-motion media query present');
    assert.match(block, /\.card:hover[\s\S]*transform:\s*none/);
    assert.match(block, /\.status-dot[\s\S]*animation:\s*none/);
    assert.match(block, /\.is-loading[\s\S]*animation:\s*none/);
});

test('topbar stats stay visible on narrow screens (#155)', () => {
    const block = mediaBlock('max-width: 1024px');
    assert.ok(block);
    assert.ok(!/\.topbar-stats\s*\{\s*display:\s*none/.test(block), 'stats are not hidden');
    assert.match(block, /\.topbar-stats\s*\{[^}]*flex-wrap:\s*wrap/);
});

// --- #349 / #362 refresh & retry -----------------------------------------
test('manual refresh shows busy state and retry banner exists', () => {
    assert.match(body, /id="manual-refresh"/);
    assert.match(body, /id="fetch-error-banner"[^>]*role="alert"[^>]*hidden/);
    assert.match(body, /id="retry-fetch"/);
    assert.match(app, /function setRefreshBusy\(busy\)/);
    assert.match(app, /retryBtn\.addEventListener\('click', requestRefresh\)/);
    assert.match(app, /showFetchError\(failure\.message\)/);
    assert.match(app, /hideFetchError\(\);/);
});

// --- #351 stale ----------------------------------------------------------
test('staleState: stale after 3 intervals, never before first success', () => {
    assert.deepStrictEqual(staleState(null, 10000, 5000), { stale: false, ageMs: null });
    assert.strictEqual(staleState(1000, 1000 + 15000, 5000).stale, false);
    assert.strictEqual(staleState(1000, 1000 + 15001, 5000).stale, true, '5s interval: stale within ~15-20s');
    assert.strictEqual(staleState(1000, 1000 + 60000, 30000).stale, false, 'scales with interval');
    assert.strictEqual(staleState(1000, 1000 + 20000, 5000, 4).stale, false);
    assert.match(body, /id="stale-badge"[^>]*hidden/);
    assert.match(body, /id="topbar-stale"[^>]*hidden/);
    assert.match(app, /renderStale\(\);/);
});

// --- #352 loading skeleton -----------------------------------------------
test('first load shows placeholders, not authoritative zeros', () => {
    assert.match(css, /\.is-loading\s*\{[^}]*color:\s*transparent/);
    assert.match(app, /function markLoading\(\)/);
    assert.match(app, /markLoading\(\);\s*\n\s*initializeCharts\(\);/);
    assert.match(app, /finishLoading\(false\);/);
    assert.match(app, /if \(!hasEverSucceeded\) finishLoading\(true\);/);
    assert.match(body, /id="topbar-status">Loading…</);
});
