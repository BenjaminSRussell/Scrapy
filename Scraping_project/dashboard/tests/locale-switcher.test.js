// Run: node --test Scraping_project/dashboard/tests/
// #1007: footer locale switcher. English is enabled and a no-op; others
// are disabled and labelled Coming soon.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { applyLocaleChoice, LOCALE_CHOICES, ENABLED_LOCALE } = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function footerSelect() {
    const footerAt = html.indexOf('<footer class="site-footer">');
    assert.ok(footerAt > html.indexOf('</main>'), 'footer follows main');
    const slice = html.slice(footerAt, html.indexOf('</footer>', footerAt));
    const select = slice.match(/<select\b[^>]*id="locale-switcher"[^>]*>([\s\S]*?)<\/select>/);
    assert.ok(select, 'locale select in the footer');
    assert.ok(!/hidden|visually-hidden|display:\s*none/.test(slice), 'switcher is not hidden');
    const options = [...select[1].matchAll(/<option\b([^>]*)>([^<]*)<\/option>/g)].map(m => ({
        attrs: m[1],
        text: m[2].trim(),
        value: (m[1].match(/value="([^"]*)"/) || [])[1],
        disabled: /\bdisabled\b/.test(m[1]),
        selected: /\bselected\b/.test(m[1]),
    }));
    return options;
}

test('footer select shows en enabled and the rest disabled Coming soon', () => {
    const options = footerSelect();
    assert.deepStrictEqual(options.map(o => o.value), LOCALE_CHOICES.map(c => c.code));
    assert.strictEqual(ENABLED_LOCALE, 'en');
    const enabled = options.filter(o => !o.disabled);
    assert.strictEqual(enabled.length, 1);
    assert.strictEqual(enabled[0].value, 'en');
    assert.strictEqual(enabled[0].selected, true);
    assert.strictEqual(enabled[0].text, 'English');
    for (const o of options) {
        if (o.value === 'en') continue;
        assert.strictEqual(o.disabled, true, o.value);
        assert.strictEqual(o.selected, false);
        assert.match(o.text, /Coming soon$/);
    }
    assert.match(html, /<label class="locale-switcher-label" for="locale-switcher">/);
});

test('selecting en is a no-op; anything else stays on en', () => {
    assert.deepStrictEqual(applyLocaleChoice('en'), { locale: 'en', changed: false });
    assert.strictEqual(applyLocaleChoice('en').rejected, undefined);
    for (const code of ['es', 'fr', 'de', 'zh', 'en-US', '', null, undefined, 'EN']) {
        const result = applyLocaleChoice(code);
        assert.strictEqual(result.locale, 'en', String(code));
        assert.strictEqual(result.changed, false);
        assert.strictEqual(result.rejected, true);
    }
});

test('the page wires the switcher without reloading or storing a locale', () => {
    assert.match(app, /function setupLocaleSwitcher\(/);
    assert.match(app, /applyLocaleChoice\(select\.value\)/);
    assert.match(app, /setupLocaleSwitcher\(\)/);
    const fn = app.slice(app.indexOf('function setupLocaleSwitcher'), app.indexOf('// #1015:'));
    assert.ok(!/localStorage|location\.|documentElement\.lang|innerHTML/.test(fn), fn);
});
