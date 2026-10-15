// Run: node --test Scraping_project/dashboard/tests/
// #988: every target=_blank anchor needs rel=noopener (tabnabbing).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { EXTERNAL_LINK_REL, applyExternalLink } = require('../format-utils.js');

const dash = path.join(__dirname, '..');

function stripJsComments(src) {
    return src
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/(^|[^:\\])\/\/.*$/gm, '$1');
}

function anchorTags(html) {
    const src = String(html).replace(/<!--[\s\S]*?-->/g, '');
    return src.match(/<a\b[^>]*>/gi) || [];
}

function attrValue(tag, name) {
    const re = new RegExp(
        '\\b' + name + '\\s*=\\s*(?:"([^"]*)"|\'([^\']*)\'|([^\\s"\'>]+))',
        'i'
    );
    const m = tag.match(re);
    if (!m) return null;
    return m[1] != null ? m[1] : (m[2] != null ? m[2] : m[3]);
}

function blankAnchorsMissingNoopener(html) {
    const bad = [];
    for (const tag of anchorTags(html)) {
        const target = attrValue(tag, 'target');
        if (target == null || target.toLowerCase() !== '_blank') continue;
        const rel = (attrValue(tag, 'rel') || '').toLowerCase().split(/\s+/).filter(Boolean);
        if (!rel.includes('noopener')) bad.push(tag);
    }
    return bad;
}

function blankTargetAssignments(src) {
    const code = stripJsComments(src);
    const dotted = code.match(/\.target\s*=\s*['"]_blank['"]/g) || [];
    const setAttr = code.match(/setAttribute\(\s*['"]target['"]\s*,\s*['"]_blank['"]\s*\)/g) || [];
    const markup = [];
    const markupRe = /target\s*=\s*['"]_blank['"]/g;
    let m;
    while ((m = markupRe.exec(code))) {
        if (m.index > 0 && code[m.index - 1] === '.') continue;
        markup.push(m[0]);
    }
    return { dotted, setAttr, markup };
}

test('applyExternalLink sets target=_blank and rel=noopener noreferrer (#988)', () => {
    assert.strictEqual(EXTERNAL_LINK_REL, 'noopener noreferrer');
    const anchor = {};
    const returned = applyExternalLink(anchor, 'https://grafana.example/d/pipeline');
    assert.strictEqual(returned, anchor);
    assert.strictEqual(anchor.href, 'https://grafana.example/d/pipeline');
    assert.strictEqual(anchor.target, '_blank');
    assert.strictEqual(anchor.rel, 'noopener noreferrer');
    const tokens = anchor.rel.split(/\s+/);
    assert.ok(tokens.includes('noopener'));
    assert.ok(tokens.includes('noreferrer'));
});

test('applyExternalLink leaves href alone when none is passed and rejects a non-element (#988)', () => {
    const anchor = { href: 'https://docs.example/runbook' };
    applyExternalLink(anchor);
    assert.strictEqual(anchor.href, 'https://docs.example/runbook');
    assert.strictEqual(anchor.rel, EXTERNAL_LINK_REL);
    assert.throws(() => applyExternalLink(null, 'https://example.edu'), TypeError);
    assert.throws(() => applyExternalLink('a', 'https://example.edu'), TypeError);
});

test('blank-target audit accepts noopener and flags a bare target=_blank (#988)', () => {
    assert.deepStrictEqual(blankAnchorsMissingNoopener(
        '<a href="https://grafana.example" target="_blank" rel="noopener noreferrer">Grafana</a>'
    ), []);
    assert.deepStrictEqual(blankAnchorsMissingNoopener(
        '<a rel="noreferrer noopener" target="_blank" href="https://docs.example">Docs</a>'
    ), []);
    assert.deepStrictEqual(blankAnchorsMissingNoopener('<a href="#main">Skip</a>'), []);
    const bare = '<a href="https://grafana.example" target="_blank">';
    assert.deepStrictEqual(blankAnchorsMissingNoopener(bare + 'Grafana</a>'), [bare]);
    const norefOnly = '<a target="_blank" rel="noreferrer" href="https://docs.example">';
    assert.deepStrictEqual(blankAnchorsMissingNoopener(norefOnly + 'Docs</a>'), [norefOnly]);
    const commented = '<!-- <a target="_blank" href="https://x">no</a> -->'
        + '<a href="#jobs">jobs</a>';
    assert.deepStrictEqual(blankAnchorsMissingNoopener(commented), []);
});

test('dashboard HTML and scripts keep every new-tab link on the helper (#988)', () => {
    const pages = fs.readdirSync(dash).filter(name => name.endsWith('.html'));
    assert.ok(pages.includes('index.html'));
    for (const name of pages) {
        const html = fs.readFileSync(path.join(dash, name), 'utf8');
        assert.deepStrictEqual(blankAnchorsMissingNoopener(html), [], name);
    }
    const scripts = fs.readdirSync(dash).filter(name => name.endsWith('.js'));
    assert.ok(scripts.includes('format-utils.js'));
    assert.ok(scripts.includes('app.js'));
    for (const name of scripts) {
        const src = fs.readFileSync(path.join(dash, name), 'utf8');
        const hits = blankTargetAssignments(src);
        if (name === 'format-utils.js') {
            assert.deepStrictEqual(hits, {
                dotted: [".target = '_blank'"],
                setAttr: [],
                markup: [],
            });
            continue;
        }
        assert.deepStrictEqual(hits, { dotted: [], setAttr: [], markup: [] }, name);
    }
    const app = fs.readFileSync(path.join(dash, 'app.js'), 'utf8');
    assert.match(app, /applyExternalLink\(a,\s*seg\.href\)/);
});
