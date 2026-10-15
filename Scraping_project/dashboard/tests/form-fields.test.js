// Run: node --test Scraping_project/dashboard/tests/
// #993: job/config text, select, and checkbox styles. The demo is not a tab.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const guide = fs.readFileSync(path.join(root, 'styleguide.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'form-fields.css'), 'utf8');

function rule(selector) {
    const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const m = css.match(new RegExp(esc + '\\s*\\{([^}]*)\\}'));
    return m ? m[1] : null;
}

test('text input and select meet a 44px touch height (#993)', () => {
    const body = rule('.field-control');
    assert.ok(body, '.field-control rule present');
    assert.match(body, /height:\s*44px/);
    assert.match(body, /min-height:\s*44px/);
    assert.match(body, /var\(--gray-300/);
    assert.match(body, /var\(--gray-900/);
    const focus = css.match(/\.field-control:focus-visible,[\s\S]*?\{([^}]*)\}/);
    assert.ok(focus, 'focus rule present');
    assert.match(focus[1], /var\(--primary-dark/);
    assert.match(focus[1], /var\(--primary,/);
    assert.match(rule('.field-checkbox') || '', /min-height:\s*44px/);
    assert.match(html, /href="form-fields\.css"/);
    assert.match(guide, /href="form-fields\.css"/);
});

test('demo labels are associated via for/id and the sample is not a tab (#993)', () => {
    assert.match(html, /id="form-field-styleguide" hidden/);
    assert.doesNotMatch(html, /data-tab="styleguide"|id="tab-styleguide"/);
    for (const doc of [html, guide]) {
        for (const id of ['ff-job-name', 'ff-job-profile', 'ff-job-enabled']) {
            assert.match(doc, new RegExp(`<label[^>]*\\bfor="${id}"`));
            assert.match(doc, new RegExp(`\\bid="${id}"`));
        }
        assert.match(doc, /class="field-control" type="text" id="ff-job-name"/);
        assert.match(doc, /<select class="field-control" id="ff-job-profile"/);
        assert.match(doc, /class="field-checkbox" type="checkbox" id="ff-job-enabled"/);
    }
});
