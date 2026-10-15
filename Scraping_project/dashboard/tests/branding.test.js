// Run: node --test Scraping_project/dashboard/tests/
// #951: UConn in the title and subtitle is the default, not the only brand.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('path');
const { sanitizeSiteName, resolveSiteBranding, brandCopy } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

test('shipped index.html is the UConn default and is not pre-branded', () => {
    assert.match(html, /<title>Pipeline Control Center - UConn Scraping<\/title>/);
    assert.match(html, /<p class="title-full">UConn Web Scraping Pipeline - Real-time Operations Dashboard<\/p>/);
    assert.ok(!/data-site-name/.test(html));
    const copy = brandCopy('UConn');
    assert.strictEqual(copy.productName, 'UConn Web Scraping Pipeline');
    assert.strictEqual(copy.subtitle, 'UConn Web Scraping Pipeline - Real-time Operations Dashboard');
    assert.strictEqual(copy.title, 'Pipeline Control Center - UConn Scraping');
});

test('a site token rewrites the product name and the tab title', () => {
    const copy = brandCopy('Acme');
    assert.strictEqual(copy.subtitle, 'Acme Web Scraping Pipeline - Real-time Operations Dashboard');
    assert.strictEqual(copy.title, 'Pipeline Control Center - Acme Scraping');
});

test('empty, too long, and markup names do not replace the default', () => {
    for (const bad of ['', '   ', 'x'.repeat(81), '<script>', 'Acme <b>', 'a\u0000b', 12, null]) {
        assert.strictEqual(sanitizeSiteName(bad), '');
        assert.strictEqual(brandCopy(bad), null);
    }
    assert.strictEqual(sanitizeSiteName('x'.repeat(80)), 'x'.repeat(80));
    assert.strictEqual(sanitizeSiteName('  Acme \n Lab  '), 'Acme Lab');
    assert.strictEqual(sanitizeSiteName('A & B'), 'A & B');
});

test('?site= wins over data-site-name; a bad query falls through', () => {
    assert.strictEqual(resolveSiteBranding({ query: '?site=Acme', dataSiteName: 'Other' }), 'Acme');
    assert.strictEqual(resolveSiteBranding({ query: '', dataSiteName: 'Other' }), 'Other');
    assert.strictEqual(resolveSiteBranding({ query: '?site=<script>', dataSiteName: 'Other' }), 'Other');
    assert.strictEqual(resolveSiteBranding({}), '');
    assert.strictEqual(resolveSiteBranding({ query: '?metrics=http://127.0.0.1/m' }), '');
});

test('app.js writes the subtitle with textContent and uses it as the tab base', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    const start = src.indexOf('function applySiteBranding(');
    const end = src.indexOf('function initialize(');
    assert.ok(start > 0 && end > start);
    const fn = src.slice(start, end);
    assert.match(fn, /resolveSiteBranding\(/);
    assert.match(fn, /sub\.textContent = copy\.subtitle/);
    assert.match(fn, /baseTitle = copy\.title/);
    assert.ok(!/innerHTML/.test(fn));
    assert.match(src, /documentTitle\(state, baseTitle\)/);
    assert.match(src.slice(end, end + 400), /applySiteBranding\(\)/);
});
