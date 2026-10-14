// Run: node --test Scraping_project/dashboard/tests/
// #1006: en catalog + t(). Tab labels and the metrics error banner go through
// t(); a missing key is the key name in dev and blank in production.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { t, registerCatalog, applyI18n, devFallback } = require('../i18n.js');

const root = path.join(__dirname, '..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'strings', 'en.json'), 'utf8'));
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');

registerCatalog('en', catalog);

test('en catalog covers topbar, tabs, and errors', () => {
    for (const key of [
        'topbar.title', 'topbar.status', 'topbar.urls', 'topbar.summaries', 'topbar.loading',
        'tabs.overview', 'tabs.pipeline', 'tabs.performance', 'tabs.system', 'tabs.activity',
        'errors.fetch', 'errors.fetchDetail', 'errors.retry', 'errors.chartsUnavailable',
    ]) {
        assert.strictEqual(typeof catalog[key], 'string', key);
        assert.ok(catalog[key].length > 0, key);
    }
});

test('t() returns catalog copy and interpolates {message}', () => {
    assert.strictEqual(t('tabs.overview'), 'Overview');
    assert.strictEqual(t('tabs.pipeline'), 'Pipeline Stages');
    assert.strictEqual(t('errors.fetch'), 'Could not load metrics.');
    assert.strictEqual(
        t('errors.fetchDetail', { message: 'HTTP 503' }),
        'Could not load metrics: HTTP 503'
    );
});

test('missing key falls back to the key name in dev and is blank in production', () => {
    assert.strictEqual(devFallback('local'), true);
    assert.strictEqual(devFallback(''), true);
    assert.strictEqual(devFallback('production'), false);
    assert.strictEqual(devFallback('prod'), false);
    assert.strictEqual(t('errors.notAKey'), 'errors.notAKey');
    assert.strictEqual(t('errors.notAKey', null, 'local'), 'errors.notAKey');
    assert.strictEqual(t('errors.notAKey', null, 'production'), '');
});

test('tab labels and the error banner are wired through t()', () => {
    for (const key of ['tabs.overview', 'tabs.pipeline', 'tabs.performance', 'tabs.system', 'tabs.activity']) {
        assert.match(html, new RegExp(`data-i18n="${key}"`));
    }
    assert.match(html, /id="fetch-error-text"[^>]*data-i18n="errors\.fetch"/);
    assert.match(html, /id="retry-fetch"[^>]*data-i18n="errors\.retry"/);
    assert.match(html, /src="i18n\.js"/);
    assert.match(app, /function i18n\(key, fallback, vars\)[\s\S]*return t\(key, vars\)/);
    assert.match(app, /i18n\('errors\.fetchDetail'/);
    assert.match(app, /i18n\('topbar\.loading'/);
    assert.match(app, /applyI18n\(/);
    const used = new Set();
    for (const source of [html, app]) {
        for (const m of source.matchAll(/data-i18n(?:-aria)?="([^"]+)"|\bi18n\(\s*'([^']+)'/g)) {
            used.add(m[1] || m[2]);
        }
    }
    for (const key of used) {
        assert.strictEqual(typeof catalog[key], 'string', 'missing catalog entry ' + key);
    }
});

test('applyI18n fills tab label and error banner from the catalog', () => {
    function el(attrs, text) {
        return {
            attrs: Object.assign({}, attrs),
            text,
            getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
            setAttribute(k, v) { this.attrs[k] = v; },
            set textContent(v) { this.text = v; },
            get textContent() { return this.text; },
        };
    }
    const tab = el({ 'data-i18n': 'tabs.system' }, '');
    const banner = el({ 'data-i18n': 'errors.fetch' }, '');
    const retry = el({ 'data-i18n': 'errors.retry', 'data-i18n-aria': 'errors.retryAria' }, '');
    const missing = el({ 'data-i18n': 'errors.notAKey' }, 'placeholder');
    applyI18n({ querySelectorAll: () => [tab, banner, retry, missing] });
    assert.strictEqual(tab.textContent, 'System Health');
    assert.strictEqual(banner.textContent, 'Could not load metrics.');
    assert.strictEqual(retry.textContent, 'Retry');
    assert.strictEqual(retry.attrs['aria-label'], 'Retry loading metrics now');
    assert.strictEqual(missing.textContent, 'errors.notAKey');
});

test('applyI18n replaces a tab label without dropping the decorative emoji', () => {
    const emoji = { nodeType: 1, getAttribute(k) { return k === 'aria-hidden' ? 'true' : null; } };
    const kids = [emoji, { nodeType: 3, textContent: ' Overview' }];
    const el = {
        attrs: { 'data-i18n': 'tabs.overview' },
        childNodes: kids,
        get firstChild() { return kids[0] || null; },
        removeChild(n) { const i = kids.indexOf(n); if (i >= 0) kids.splice(i, 1); },
        appendChild(n) { kids.push(n); return n; },
        getAttribute(k) { return this.attrs[k] || null; },
        setAttribute(k, v) { this.attrs[k] = v; },
        ownerDocument: { createTextNode(v) { return { nodeType: 3, textContent: v }; } },
        set textContent(v) { throw new Error('textContent would drop the emoji: ' + v); },
    };
    applyI18n({ querySelectorAll: () => [el] });
    assert.strictEqual(kids[0], emoji);
    assert.strictEqual(kids[1].textContent, ' Overview');
});
