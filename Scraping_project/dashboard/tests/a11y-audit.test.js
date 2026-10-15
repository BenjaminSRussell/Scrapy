// Run: node --test Scraping_project/dashboard/tests/a11y-audit.test.js
// #950: accessibility audit mode highlights findings, uses axe-core when it
// loads, and exports a JSON report. The page still audits if axe-core is absent.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const audit = require('../a11y-audit.js');
const utils = require('../format-utils.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));

function makeEl(tag, attrs, kids) {
    const el = {
        nodeType: 1,
        tagName: tag.toUpperCase(),
        attrs: Object.assign({}, attrs),
        children: [],
        parentElement: null,
    };
    el.getAttribute = (name) => (Object.prototype.hasOwnProperty.call(el.attrs, name) ? String(el.attrs[name]) : null);
    el.hasAttribute = (name) => Object.prototype.hasOwnProperty.call(el.attrs, name);
    el.setAttribute = (name, value) => { el.attrs[name] = String(value); };
    el.removeAttribute = (name) => { delete el.attrs[name]; };
    const classes = new Set();
    el.classList = {
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c),
        contains: (c) => classes.has(c),
    };
    Object.defineProperty(el, 'textContent', {
        get() {
            return el.children.map(child => (typeof child === 'string' ? child : child.textContent)).join('');
        },
    });
    for (const kid of kids || []) {
        if (typeof kid === 'string') el.children.push(kid);
        else {
            kid.parentElement = el;
            el.children.push(kid);
        }
    }
    return el;
}

function isUnder(ancestor, node) {
    let current = node;
    while (current) {
        if (current === ancestor) return true;
        current = current.parentElement;
    }
    return false;
}

function makeDoc(root, title) {
    const all = [];
    (function walk(node) {
        if (!node || !node.tagName) return;
        all.push(node);
        for (const child of node.children) if (child && child.tagName) walk(child);
    })(root);
    const htmlEl = root.tagName === 'HTML' ? root : null;
    const body = all.find(node => node.tagName === 'BODY') || root;
    body.querySelectorAll = (sel) => {
        if (sel === '*') return all.filter(node => node !== htmlEl && node !== body && isUnder(body, node));
        if (sel === '.cc-a11y-hit') return all.filter(node => node.classList && node.classList.contains('cc-a11y-hit'));
        throw new Error(`unexpected selector ${sel}`);
    };
    const doc = {
        title: title || '',
        documentElement: htmlEl,
        body,
        getElementById(id) {
            return all.find(node => node.getAttribute('id') === id) || null;
        },
        querySelector(sel) {
            const labelled = /^label\[for="(.*)"\]$/.exec(sel);
            if (labelled) {
                return all.find(node => node.tagName === 'LABEL' && node.getAttribute('for') === labelled[1]) || null;
            }
            if (sel.startsWith('#')) return doc.getElementById(sel.slice(1));
            return body.querySelectorAll(sel)[0] || null;
        },
        querySelectorAll: (sel) => body.querySelectorAll(sel),
    };
    return doc;
}

function ids(issues) {
    return issues.map(issue => issue.id);
}

test('built-in checks flag the controls an operator can see and skip the quiet ones', () => {
    const unnamed = makeEl('button', { id: 'go' });
    const named = makeEl('button', { id: 'ok', 'aria-label': 'Save' });
    const picture = makeEl('img', { id: 'pic' });
    const decorative = makeEl('img', { id: 'deco', alt: '' });
    const link = makeEl('a', { id: 'out', href: '/next' });
    const anchor = makeEl('a', { id: 'anchor' });
    const bare = makeEl('input', { id: 'q', type: 'search', placeholder: 'Filter' });
    const wrapped = makeEl('input', { id: 'email', type: 'email' });
    const label = makeEl('label', { for: 'email' }, ['Work email', wrapped]);
    const titled = makeEl('input', { id: 'note', type: 'text', title: 'Note' });
    const submit = makeEl('input', { id: 'send', type: 'submit' });
    const emptyHeading = makeEl('h2', { id: 'blank' });
    const heading = makeEl('h2', { id: 'real' }, ['Overview']);
    const tabby = makeEl('div', { id: 'tabby', tabindex: '3' });
    const natural = makeEl('div', { id: 'natural', tabindex: '0' });
    const frame = makeEl('iframe', { id: 'frame' });
    const icon = makeEl('svg', { id: 'icon', role: 'img' });
    const hiddenIcon = makeEl('svg', { id: 'hidden-icon', role: 'img', 'aria-hidden': 'true' });
    const hiddenImg = makeEl('img', { id: 'hidden-img' });
    const hiddenWrap = makeEl('div', { id: 'gone', hidden: '' }, [hiddenImg]);
    const stealth = makeEl('button', { id: 'stealth', 'aria-hidden': 'true' }, ['Secret']);
    const dupA = makeEl('span', { id: 'dup' }, ['A']);
    const dupB = makeEl('span', { id: 'dup' }, ['B']);
    const body = makeEl('body', {}, [
        unnamed, named, picture, decorative, link, anchor, bare, label, titled, submit,
        emptyHeading, heading, tabby, natural, frame, icon, hiddenIcon, hiddenWrap, stealth,
        dupA, dupB,
    ]);
    const doc = makeDoc(makeEl('html', {}, [body]), '');
    const found = audit.judgeA11yCandidates(audit.collectA11yInput(doc).candidates, { title: '', lang: '' });
    const byId = new Map(found.map(issue => [`${issue.id}|${issue.selector}`, issue]));

    assert.ok(byId.has('button-name|#go'));
    assert.ok(!found.some(issue => issue.selector === '#ok'));
    assert.strictEqual(byId.get('image-alt|#pic').impact, 'critical');
    assert.ok(!found.some(issue => issue.id === 'image-alt' && issue.selector === '#deco'));
    assert.ok(byId.has('link-name|#out'));
    assert.ok(!found.some(issue => issue.selector === '#anchor'));
    assert.ok(byId.has('label|#q'), 'placeholder is not a label');
    assert.ok(!found.some(issue => issue.selector === '#email'));
    assert.ok(!found.some(issue => issue.selector === '#note'));
    assert.ok(!found.some(issue => issue.id === 'button-name' && issue.selector === '#send'));
    assert.ok(byId.has('empty-heading|#blank'));
    assert.ok(!found.some(issue => issue.selector === '#real'));
    assert.ok(byId.has('tabindex|#tabby'));
    assert.ok(!found.some(issue => issue.id === 'tabindex' && issue.selector === '#natural'));
    assert.ok(byId.has('frame-title|#frame'));
    assert.ok(byId.has('image-alt|#icon'));
    assert.ok(!found.some(issue => issue.selector === '#hidden-icon'));
    assert.ok(!found.some(issue => issue.id === 'image-alt' && issue.selector === '#hidden-img'));
    assert.ok(byId.has('aria-hidden-focus|#stealth'));
    assert.ok(!found.some(issue => issue.id === 'button-name' && issue.selector === '#stealth'));
    const dups = found.filter(issue => issue.id === 'duplicate-id');
    assert.strictEqual(dups.length, 2);
    assert.ok(dups.every(issue => issue.selector !== '#dup'));
    assert.ok(dups.every(issue => /Duplicate id "dup"/.test(issue.message)));
    assert.ok(ids(found).includes('document-title'));
    assert.ok(ids(found).includes('html-has-lang'));
    assert.ok(found.every(issue => issue.source === 'builtin'));
    assert.ok(found.every(issue => issue.helpUrl.startsWith('https://')));
});

test('a titled document with a lang does not raise document-level findings', () => {
    const body = makeEl('body', {}, [makeEl('p', { id: 'p' }, ['Hello'])]);
    const doc = makeDoc(makeEl('html', { lang: 'en' }, [body]), 'Pipeline Control Center');
    const input = audit.collectA11yInput(doc);
    const found = audit.judgeA11yCandidates(input.candidates, input);
    assert.deepStrictEqual(found, []);
    assert.strictEqual(input.lang, 'en');
    assert.strictEqual(input.title, 'Pipeline Control Center');
});

test('axe violations merge with built-in findings and a bad help URL is dropped', () => {
    const issues = audit.issuesFromAxe({
        violations: [{
            id: 'button-name',
            impact: 'serious',
            help: 'Buttons must have discernible text',
            helpUrl: 'javascript:alert(1)',
            nodes: [{
                impact: 'critical',
                target: ['#go'],
                failureSummary: 'Fix any of the following:\n  Element has no name',
            }],
        }, {
            id: 'color-contrast',
            impact: 'serious',
            help: 'Contrast is too low',
            helpUrl: 'https://dequeuniversity.com/rules/axe/4.10/color-contrast',
            nodes: [{ target: [['iframe', '.card'], 'button.bad'], failureSummary: 'Contrast 2.1:1' }],
        }],
    });
    assert.strictEqual(issues[0].helpUrl, '');
    assert.strictEqual(issues[0].message, 'Fix any of the following:');
    assert.strictEqual(issues[0].impact, 'critical');
    assert.strictEqual(issues[0].selector, '#go');
    assert.strictEqual(issues[1].selector, 'button.bad');
    assert.strictEqual(issues[1].helpUrl, 'https://dequeuniversity.com/rules/axe/4.10/color-contrast');

    const merged = audit.mergeIssues([
        { id: 'button-name', impact: 'serious', source: 'builtin', message: 'Button has no accessible name.', selector: '#go', helpUrl: '' },
        issues[0],
        issues[1],
    ]);
    const button = merged.find(issue => issue.id === 'button-name');
    assert.strictEqual(button.source, 'axe');
    assert.strictEqual(button.impact, 'critical');
    assert.deepStrictEqual(merged.map(issue => issue.id), ['button-name', 'color-contrast']);
});

test('runA11yAudit keeps built-in results when axe-core is missing or throws', async () => {
    const body = makeEl('body', {}, [makeEl('button', { id: 'go' })]);
    const doc = makeDoc(makeEl('html', { lang: 'en' }, [body]), 'Control Center');
    const skipped = await audit.runA11yAudit(doc, { now: 0 });
    assert.strictEqual(skipped.axe, 'skipped');
    assert.ok(skipped.issues.some(issue => issue.id === 'button-name'));

    const missing = await audit.runA11yAudit(doc, { axe: null, now: 0 });
    assert.strictEqual(missing.axe, 'unavailable');
    assert.strictEqual(missing.generatedAt, '1970-01-01T00:00:00.000Z');
    assert.match(audit.a11yStatusText(missing), /axe-core unavailable/);

    const broken = await audit.runA11yAudit(doc, {
        axe: { run() { throw new Error('axe exploded'); } },
        now: 0,
    });
    assert.strictEqual(broken.axe, 'error');
    assert.strictEqual(broken.axeError, 'axe exploded');
    assert.ok(broken.issues.some(issue => issue.source === 'builtin'));
    assert.match(audit.a11yStatusText(broken), /axe-core failed/);

    const used = await audit.runA11yAudit(doc, {
        axe: {
            async run() {
                return {
                    violations: [{
                        id: 'button-name',
                        impact: 'serious',
                        help: 'Name the button',
                        helpUrl: 'https://dequeuniversity.com/rules/axe/4.10/button-name',
                        nodes: [{ target: ['#go'], failureSummary: 'Element has no title' }],
                    }, {
                        id: 'color-contrast',
                        impact: 'serious',
                        help: 'Contrast',
                        helpUrl: 'https://dequeuniversity.com/rules/axe/4.10/color-contrast',
                        nodes: [{ target: ['#go'], failureSummary: 'Low contrast' }],
                    }],
                };
            },
        },
        now: 0,
    });
    assert.strictEqual(used.axe, 'used');
    assert.strictEqual(used.issues.filter(issue => issue.id === 'button-name').length, 1);
    assert.strictEqual(used.issues.find(issue => issue.id === 'button-name').source, 'axe');
    assert.ok(used.issues.some(issue => issue.id === 'color-contrast'));
    const exported = JSON.parse(audit.a11yReportJson(used));
    assert.strictEqual(exported.issueCount, used.issues.length);
    assert.match(exported.summary, /axe-core included/);
    assert.ok(!Object.prototype.hasOwnProperty.call(exported.issues[0], 'node'));
    assert.match(audit.a11yStatusText({ axe: 'used', issueCount: 0 }), /No accessibility issues found/);
    assert.match(audit.a11yStatusText({ axe: 'used', issueCount: 1 }), /1 accessibility issue highlighted/);
});

test('highlights mark the worst impact and clear removes them', () => {
    const button = makeEl('button', { id: 'go' });
    const body = makeEl('body', {}, [button]);
    const root = makeEl('html', { lang: 'en' }, [body]);
    const doc = makeDoc(root, 'Control Center');
    doc.querySelectorAll = (sel) => {
        if (sel === '.cc-a11y-hit') return body.querySelectorAll(sel);
        if (sel === '#go') return [button];
        if (sel === 'html' || sel === 'not a selector :::') return [];
        throw new Error(`bad ${sel}`);
    };
    audit.highlightA11yIssues(doc, [
        { impact: 'minor', selector: '#go' },
        { impact: 'serious', selector: '#go' },
        { impact: 'critical', selector: 'html' },
        { impact: 'moderate', selector: 'not a selector :::' },
    ]);
    assert.ok(button.classList.contains('cc-a11y-hit'));
    assert.strictEqual(button.getAttribute('data-a11y-impact'), 'serious');
    audit.clearA11yHighlights(doc);
    assert.ok(!button.classList.contains('cc-a11y-hit'));
    assert.strictEqual(button.getAttribute('data-a11y-impact'), null);
});

function scriptHost() {
    let script = null;
    const head = {
        appendChild(node) { script = node; },
    };
    return {
        doc: {
            head,
            querySelector() { return script; },
            createElement() {
                const node = { attrs: {}, listeners: {} };
                node.setAttribute = (name, value) => { node.attrs[name] = String(value); };
                node.getAttribute = (name) => (node.attrs[name] == null ? null : node.attrs[name]);
                node.addEventListener = (type, fn) => { node.listeners[type] = fn; };
                node.remove = () => { script = null; };
                Object.defineProperty(node, 'onload', { set(fn) { node.listeners.load = fn; } });
                Object.defineProperty(node, 'onerror', { set(fn) { node.listeners.error = fn; } });
                return node;
            },
        },
        get script() { return script; },
    };
}

test('axe-core is optional: pinned script, existing global, or a failed load', async () => {
    assert.strictEqual(audit.AXE_CORE_SRC, 'https://cdn.jsdelivr.net/npm/axe-core@4.14.0/axe.min.js');
    assert.strictEqual(audit.AXE_CORE_INTEGRITY, 'sha384-lFUXB8tI4BaTE1bqxH9eeAXON2NH1qsdAhs7S5L++nUNP5QOy+hwr0C3TYEJUEl0');

    const axe = { run() { return { violations: [] }; } };
    const present = await audit.loadOptionalAxe(null, {
        window: { axe },
        injectScript() { throw new Error('should not inject'); },
    });
    assert.strictEqual(present, axe);

    let injected = false;
    const skipped = await audit.loadOptionalAxe(null, {
        load: false,
        window: {},
        injectScript() { injected = true; },
    });
    assert.strictEqual(skipped, null);
    assert.strictEqual(injected, false);

    const win = {};
    const loaded = await audit.loadOptionalAxe(null, {
        window: win,
        injectScript() { win.axe = axe; },
    });
    assert.strictEqual(loaded, axe);

    const blocked = await audit.loadOptionalAxe(null, {
        window: {},
        injectScript() { throw new Error('blocked'); },
    });
    assert.strictEqual(blocked, null);

    const host = scriptHost();
    const page = {};
    const pending = audit.loadOptionalAxe(host.doc, { window: page });
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(host.script, 'pinned axe-core script is injected');
    assert.strictEqual(host.script.src, audit.AXE_CORE_SRC);
    assert.strictEqual(host.script.integrity, audit.AXE_CORE_INTEGRITY);
    assert.strictEqual(host.script.crossOrigin, 'anonymous');
    assert.strictEqual(host.script.getAttribute('data-cc-axe'), '1');
    page.axe = axe;
    host.script.listeners.load();
    assert.strictEqual(await pending, axe);

    const failedHost = scriptHost();
    const failed = audit.loadOptionalAxe(failedHost.doc, { window: {} });
    await new Promise(resolve => setImmediate(resolve));
    failedHost.script.listeners.error();
    assert.strictEqual(await failed, null);
    assert.strictEqual(failedHost.script, null);
});

test('the refresh bar exposes the audit toggle and a disabled export (#950)', () => {
    assert.match(html, /id="a11y-audit-toggle"[^>]*aria-pressed="false"[^>]*aria-describedby="a11y-audit-hint"/);
    assert.match(html, /id="a11y-audit-export"[^>]*disabled[^>]*aria-describedby="a11y-audit-hint"/);
    assert.match(html, /id="a11y-audit-status"[^>]*role="status"[^>]*aria-live="polite"[^>]*hidden/);
    assert.match(html, /id="a11y-audit-list"[^>]*aria-label="Accessibility audit results"[^>]*hidden/);
    assert.match(html, /<script src="a11y-audit.js"><\/script>\s*<script src="version.js"><\/script>\s*<script src="app.js"><\/script>/);
    assert.doesNotMatch(html, /<script[^>]*axe-core/, 'axe-core is not a blocking script tag');
    assert.match(css, /\.cc-a11y-hit\s*\{[^}]*outline:\s*3px solid #b45309/);
    assert.match(css, /\.cc-a11y-hit\[data-a11y-impact="critical"\]/);
    const forcedStart = css.indexOf('@media (forced-colors: active)');
    assert.ok(forcedStart > 0);
    const forced = css.slice(forcedStart);
    assert.match(forced, /\.cc-a11y-hit\s*\{[^}]*outline:\s*3px solid Highlight/);
});

test('app.js wires the toggle, highlights, and JSON export without innerHTML', () => {
    const start = app.indexOf('function renderA11yAudit');
    const end = app.indexOf('function applyEnvBadge');
    assert.ok(start > 0 && end > start);
    const fn = app.slice(start, end);
    assert.ok(!fn.includes('innerHTML'));
    assert.match(fn, /loadOptionalAxe\(document\)/);
    assert.match(fn, /runA11yAudit\(document,\s*\{\s*axe:\s*axe \|\| null/);
    assert.match(fn, /highlightA11yIssues\(document, report\.issues\)/);
    assert.match(fn, /clearA11yHighlights\(document\)/);
    assert.match(fn, /downloadBlob\('a11y-audit\.json', 'application\/json', a11yReportJson\(a11yReport\)\)/);
    assert.match(fn, /a11yHelpLink\(issue\.helpUrl\)/);
    assert.match(app, /typeof a11yHelpUrl === 'function'/);
    assert.match(app, /setupA11yAudit\(\)/);
});

test('turning audit mode on highlights and export, and off clears them', async () => {
    const listeners = {};
    const attrs = {};
    const status = { hidden: true, textContent: '' };
    const list = {
        hidden: true,
        children: [],
        replaceChildren() { this.children = []; },
        append(...nodes) { this.children.push(...nodes); },
    };
    const exportBtn = { disabled: true, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {} };
    const toggle = {
        attrs,
        setAttribute(k, v) { attrs[k] = String(v); },
        addEventListener(type, fn) { listeners[type] = fn; },
    };
    function element(tag) {
        const node = {
            tagName: tag.toUpperCase(),
            type: '',
            className: '',
            textContent: '',
            hidden: false,
            children: [],
            attrs: {},
            setAttribute(k, v) { this.attrs[k] = String(v); },
            append(...kids) { this.children.push(...kids); },
            addEventListener() {},
        };
        return node;
    }
    const document = {
        readyState: 'loading',
        addEventListener() {},
        getElementById(id) {
            if (id === 'a11y-audit-toggle') return toggle;
            if (id === 'a11y-audit-export') return exportBtn;
            if (id === 'a11y-audit-status') return status;
            if (id === 'a11y-audit-list') return list;
            return null;
        },
        createElement: element,
        createTextNode(text) { return { nodeType: 3, textContent: text }; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
    let highlighted = null;
    let cleared = 0;
    const axe = { async run() { return { violations: [] }; } };
    const ctx = {
        ...utils,
        document,
        window: {},
        console: { log() {}, warn() {}, error() {} },
        URL: { createObjectURL() { return 'blob:1'; }, revokeObjectURL() {} },
        Blob,
        clearTimeout,
        setTimeout,
        loadOptionalAxe: async () => axe,
        highlightA11yIssues(_doc, issues) { highlighted = issues; },
        clearA11yHighlights() { cleared += 1; },
        a11yStatusText: audit.a11yStatusText,
        a11yReportJson: audit.a11yReportJson,
        a11yHelpUrl: audit.a11yHelpUrl,
        runA11yAudit: async (_doc, opts) => ({
            generatedAt: '2020-01-01T00:00:00.000Z',
            axe: opts.axe ? 'used' : 'unavailable',
            axeError: null,
            issueCount: 2,
            issues: [
                { id: 'button-name', impact: 'serious', source: 'builtin', message: 'Button has no accessible name.', selector: '#go', helpUrl: 'https://dequeuniversity.com/rules/axe/4.10/button-name' },
                { id: 'label', impact: 'serious', source: 'builtin', message: 'Form field has no label.', selector: '#q', helpUrl: 'javascript:alert(1)' },
            ],
        }),
    };
    vm.createContext(ctx);
    vm.runInContext(app, ctx);
    ctx.finished = null;
    vm.runInContext('finished = setA11yAuditMode(true)', ctx);
    await ctx.finished;
    assert.strictEqual(attrs['aria-pressed'], 'true');
    assert.strictEqual(attrs['aria-busy'], 'false');
    assert.strictEqual(exportBtn.disabled, false);
    assert.strictEqual(status.hidden, false);
    assert.match(status.textContent, /2 accessibility issues highlighted \(axe-core included\)/);
    assert.strictEqual(highlighted.length, 2);
    assert.strictEqual(list.hidden, false);
    assert.strictEqual(list.children.length, 2);
    const help = list.children[0].children.find(child => child.tagName === 'A');
    assert.strictEqual(help.href, 'https://dequeuniversity.com/rules/axe/4.10/button-name');
    assert.strictEqual(help.rel, 'noopener noreferrer');
    assert.ok(!list.children[1].children.some(child => child.tagName === 'A'));
    vm.runInContext('finished = setA11yAuditMode(false)', ctx);
    await ctx.finished;
    assert.strictEqual(attrs['aria-pressed'], 'false');
    assert.strictEqual(exportBtn.disabled, true);
    assert.strictEqual(status.hidden, true);
    assert.strictEqual(list.hidden, true);
    assert.strictEqual(cleared, 1);
});
