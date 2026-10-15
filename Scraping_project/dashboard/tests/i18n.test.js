// Run: node --test Scraping_project/dashboard/tests/
// #955: extracted Control Center strings, en + es stub, language switcher.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const i18n = require('../i18n.js');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const en = JSON.parse(fs.readFileSync(path.join(root, 'locales', 'en.json'), 'utf8'));
const es = JSON.parse(fs.readFileSync(path.join(root, 'locales', 'es.json'), 'utf8'));

function messageKeys(catalog) {
    return Object.keys(catalog.messages).sort();
}

test('en is complete and es is a same-key stub', () => {
    assert.strictEqual(en.locale, 'en');
    assert.strictEqual(en.stub, false);
    assert.strictEqual(es.locale, 'es');
    assert.strictEqual(es.stub, true);
    assert.ok(messageKeys(en).length >= 100, 'shell strings are extracted');
    assert.deepStrictEqual(messageKeys(es), messageKeys(en));
    for (const key of messageKeys(en)) {
        assert.strictEqual(typeof en.messages[key], 'string', key);
        assert.ok(en.messages[key] !== '', key);
        assert.strictEqual(typeof es.messages[key], 'string', key);
    }
    assert.notStrictEqual(es.messages['title.full'], en.messages['title.full']);
    assert.notStrictEqual(es.messages['tab.overview'], en.messages['tab.overview']);
});

test('t() uses the active locale, falls back to en, and interpolates', () => {
    const live = i18n.createI18n({ en: en, es: es }, 'es');
    assert.strictEqual(live.getLocale(), 'es');
    assert.strictEqual(live.t('tab.overview'), es.messages['tab.overview']);
    assert.strictEqual(live.t('queue.updated', { time: '12:00' }), 'Actualizado 12:00');
    const copy = JSON.parse(JSON.stringify(es));
    delete copy.messages['title.full'];
    const fallback = i18n.createI18n({ en: en, es: copy }, 'es');
    assert.strictEqual(fallback.t('title.full'), en.messages['title.full']);
    assert.strictEqual(fallback.t('missing.key'), 'missing.key');
    assert.strictEqual(live.setLocale('fr'), 'es');
    assert.strictEqual(live.setLocale('en'), 'en');
    assert.strictEqual(live.t('tab.overview'), en.messages['tab.overview']);
});

test('locale choice is ?lang= then cc.locale, and unknown values stay en', () => {
    const mem = { value: 'es', getItem() { return this.value; }, setItem(k, v) { this.value = v; } };
    assert.strictEqual(i18n.STORAGE_KEY, 'cc.locale');
    assert.strictEqual(i18n.readStoredLocale(mem, '?lang=en'), 'en');
    assert.strictEqual(i18n.readStoredLocale(mem, ''), 'es');
    assert.strictEqual(i18n.readStoredLocale(mem, '?lang=fr'), 'es');
    assert.strictEqual(i18n.readStoredLocale({ getItem() { throw new Error('blocked'); } }, ''), 'en');
    assert.strictEqual(i18n.readStoredLocale(null, ''), 'en');
});

test('replaceOwnText keeps icons and sibling counts', () => {
    const text = { nodeType: 3, textContent: ' Overview' };
    const svg = { nodeType: 1 };
    const button = { childNodes: [svg, text] };
    i18n.replaceOwnText(button, 'Resumen');
    assert.strictEqual(text.textContent, ' Resumen');
    assert.strictEqual(svg.nodeType, 1);

    const label = { nodeType: 3, textContent: 'Success ' };
    const count = { nodeType: 1 };
    const chip = { childNodes: [label, count] };
    i18n.replaceOwnText(chip, 'Éxito');
    assert.strictEqual(label.textContent, 'Éxito ');
});

test('the page has a language switcher and loads the catalog before app.js', () => {
    assert.match(html, /<label[^>]*for="cc-lang"[^>]*data-i18n="lang.label"/);
    assert.match(html, /<select id="cc-lang"/);
    assert.match(html, /<option value="en"/);
    assert.match(html, /<option value="es"/);
    assert.match(html, /<script src="i18n.js"><\/script>\s*<script src="app.js"><\/script>/);
    assert.match(app, /document\.addEventListener\('cc:locale'/);
    assert.match(app, /function ccT\(key, fallback, vars\)/);
    const scripts = [...html.matchAll(/<script src="([^"]+)"/g)].map(m => m[1]);
    assert.ok(scripts.indexOf('i18n.js') < scripts.indexOf('app.js'));
});

test('every wired key exists in both catalogs and plain text matches en', () => {
    const attr = /data-i18n(?:-aria-label|-title|-placeholder)?="([^"]+)"/g;
    const wired = new Set();
    for (const m of html.matchAll(attr)) wired.add(m[1]);
    for (const m of app.matchAll(/ccT\('([^']+)'/g)) wired.add(m[1]);
    for (const m of app.matchAll(/i18nKey: '([^']+)'/g)) wired.add(m[1]);
    for (const m of app.matchAll(/_i18nLabelKeys = \[([^\]]+)\]/g)) {
        for (const key of m[1].matchAll(/'([^']+)'/g)) wired.add(key[1]);
    }
    assert.ok(wired.size >= 80, `only ${wired.size} keys wired`);
    for (const key of wired) {
        assert.ok(Object.prototype.hasOwnProperty.call(en.messages, key), 'missing en ' + key);
        assert.ok(Object.prototype.hasOwnProperty.call(es.messages, key), 'missing es ' + key);
    }
    const textRe = /data-i18n="([^"]+)"[^>]*>([^<]+)</g;
    for (const m of html.matchAll(textRe)) {
        const text = m[2].replace(/\s+/g, ' ').trim();
        if (!text) continue;
        assert.strictEqual(text, en.messages[m[1]], m[1]);
    }
});
