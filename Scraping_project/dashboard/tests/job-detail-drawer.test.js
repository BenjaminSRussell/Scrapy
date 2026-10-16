// Run: node --test Scraping_project/dashboard/tests/
// #998: job detail drawer stub. Opens and closes accessibly; fields are labeled
// placeholders and do not call a jobs API.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const body = html.slice(html.indexOf('<body'));
const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));

function section(name) {
    const re = new RegExp(
        `<section class="job-drawer-section"[^>]*>\\s*<h3[^>]*>${name}</h3>([\\s\\S]*?)</section>`
    );
    const m = body.match(re);
    assert.ok(m, `section ${name} present`);
    return m[1];
}

test('drawer is a named modal pinned to the right edge', () => {
    assert.match(body, /<dialog id="job-detail-drawer" class="job-drawer" role="dialog" aria-modal="true" aria-labelledby="job-detail-title">/);
    assert.match(body, /<h2 id="job-detail-title" class="job-drawer-title">Job details<\/h2>/);
    const rules = [...css.matchAll(/\.job-drawer\s*\{([^}]*)\}/g)].map(m => m[1]);
    const rule = rules.find(r => /position:\s*fixed/.test(r));
    assert.ok(rule, '.job-drawer positioning rule present');
    assert.match(rule, /right:\s*0/);
    assert.match(rule, /left:\s*auto/);
    assert.match(rule, /width:\s*min\(100%,\s*24rem\)/);
    assert.match(rule, /max-width:\s*100%/);
    assert.match(css, /\.job-drawer::backdrop\s*\{/);
});

test('opener and close control expose dialog semantics', () => {
    assert.match(body, /id="open-job-detail"[^>]*aria-haspopup="dialog"[^>]*aria-controls="job-detail-drawer"[^>]*aria-expanded="false"/);
    assert.match(body, /id="job-detail-close"[^>]*data-drawer-initial="true"[^>]*aria-label="Close job details"/);
});

test('Status, Profile, Timestamps and Actions are labeled placeholders', () => {
    const expected = {
        Status: ['State', 'Stage', 'Progress'],
        Profile: ['Profile name', 'Spider', 'Seed URL'],
        Timestamps: ['Created', 'Started', 'Finished'],
    };
    for (const [name, labels] of Object.entries(expected)) {
        const block = section(name);
        const fields = [...block.matchAll(/<dt[^>]*>([^<]+)<\/dt>\s*<dd class="job-placeholder">Placeholder<\/dd>/g)]
            .map(m => m[1]);
        assert.deepStrictEqual(fields, labels, name);
    }
    const actions = section('Actions');
    assert.match(actions, /id="job-detail-actions-note"/);
    for (const label of ['Pause', 'Stop', 'Retry']) {
        assert.match(actions, new RegExp(`<button type="button" class="btn" disabled aria-describedby="job-detail-actions-note">${label}</button>`));
    }
});

test('open and close are wired without a jobs API or innerHTML', () => {
    const start = app.indexOf('function setupJobDetailDrawer');
    const end = app.indexOf('\nfunction initialize()');
    const fn = app.slice(start, end);
    assert.ok(start >= 0 && end > start, 'setup function present');
    assert.match(fn, /dialog\.showModal\(\)/);
    assert.match(fn, /dialog\.close\(\)/);
    assert.match(fn, /setAttribute\('aria-expanded', 'true'\)/);
    assert.match(fn, /setAttribute\('aria-expanded', 'false'\)/);
    assert.match(fn, /event\.key !== 'Escape'/);
    assert.match(fn, /event\.target === dialog/);
    assert.match(fn, /back\.focus\(\)/);
    assert.match(app, /setupStageDrilldown\(\);\s*\n\s*setupJobDetailDrawer\(\);\s*\n\s*setupShortcuts\(\)/);
    assert.match(fn, /closeStageDrilldown\(\)/);
    assert.match(app, /jobDrawer\.open\) return/);
    assert.ok(!/innerHTML/.test(fn), 'drawer must not write HTML strings');
    assert.ok(!/fetch\(/.test(fn), 'drawer must not call the network');
});
