// Run: node --test Scraping_project/dashboard/tests/
// Bookmark and mobile chrome: description and theme-color (#968).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const head = html.slice(html.indexOf('<head'), html.indexOf('</head>'));

test('head has a description and a theme-color matching --primary (#968)', () => {
    const primary = head.match(/--primary:\s*(#[0-9a-fA-F]{3,8})/);
    assert.ok(primary, '--primary color present');
    const desc = head.match(/<meta\s+name="description"\s+content="([^"]+)"/);
    assert.ok(desc, 'meta description present');
    assert.ok(desc[1].length > 20 && desc[1].length <= 160, desc[1]);
    assert.match(desc[1], /Control Center/);
    const theme = head.match(/<meta\s+name="theme-color"\s+content="([^"]+)"/);
    assert.ok(theme, 'theme-color present');
    assert.strictEqual(theme[1].toLowerCase(), primary[1].toLowerCase());
});
