// Run: node --test Scraping_project/dashboard/tests/
// #995: destructive job confirm. Focus stays in the dialog; Esc and Cancel
// dismiss; Confirm runs its callback once and returns focus to the opener.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createConfirmDialog } = require('../modal.js');

function createDoc() {
    const doc = {
        activeElement: null,
        listeners: {},
        body: null,
    };

    function Fake(tag) {
        this.tagName = String(tag).toUpperCase();
        this.children = [];
        this.parentElement = null;
        this.attrs = {};
        this.id = '';
        this.className = '';
        this.hidden = false;
        this.disabled = false;
        this.inert = false;
        this.textContent = '';
        this.style = {};
        this.listeners = {};
    }
    Fake.prototype.setAttribute = function (name, value) {
        this.attrs[name] = String(value);
        if (name === 'id') this.id = String(value);
        if (name === 'class') this.className = String(value);
        if (name === 'hidden') this.hidden = true;
        if (name === 'disabled') this.disabled = true;
    };
    Fake.prototype.getAttribute = function (name) {
        return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    };
    Fake.prototype.hasAttribute = function (name) {
        return Object.prototype.hasOwnProperty.call(this.attrs, name);
    };
    Fake.prototype.removeAttribute = function (name) {
        delete this.attrs[name];
        if (name === 'hidden') this.hidden = false;
        if (name === 'disabled') this.disabled = false;
    };
    Fake.prototype.appendChild = function (child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
    };
    Fake.prototype.contains = function (node) {
        if (node === this) return true;
        for (let i = 0; i < this.children.length; i++) {
            if (this.children[i].contains(node)) return true;
        }
        return false;
    };
    Fake.prototype.addEventListener = function (type, fn) {
        (this.listeners[type] || (this.listeners[type] = [])).push(fn);
    };
    Fake.prototype.dispatchEvent = function (event) {
        if (!event.target) event.target = this;
        const list = (this.listeners[event.type] || []).slice();
        for (let i = 0; i < list.length; i++) {
            list[i].call(this, event);
            if (event.stopped) break;
        }
        return true;
    };
    Fake.prototype.focus = function () {
        if (this.disabled) return;
        let node = this;
        while (node) {
            if (node.hidden) return;
            node = node.parentElement;
        }
        doc.activeElement = this;
        doc.dispatchEvent({
            type: 'focusin',
            target: this,
            defaultPrevented: false,
            stopped: false,
            preventDefault() { this.defaultPrevented = true; },
            stopPropagation() { this.stopped = true; },
        });
    };

    doc.createElement = function (tag) { return new Fake(tag); };
    doc.body = doc.createElement('body');
    doc.activeElement = doc.body;
    doc.addEventListener = function (type, fn) {
        (doc.listeners[type] || (doc.listeners[type] = [])).push(fn);
    };
    doc.dispatchEvent = function (event) {
        const list = (doc.listeners[event.type] || []).slice();
        for (let i = 0; i < list.length; i++) {
            list[i].call(doc, event);
            if (event.stopped) break;
        }
        return true;
    };
    doc.getElementById = function (id) {
        function walk(node) {
            if (!node) return null;
            if (node.id === id) return node;
            const kids = node.children || [];
            for (let i = 0; i < kids.length; i++) {
                const found = walk(kids[i]);
                if (found) return found;
            }
            return null;
        }
        return walk(doc.body);
    };
    return doc;
}

function keyEvent(key, extra) {
    return Object.assign({
        type: 'keydown',
        key: key,
        shiftKey: false,
        ctrlKey: false,
        metaKey: false,
        altKey: false,
        defaultPrevented: false,
        stopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.stopped = true; },
    }, extra || {});
}

function clickEvent() {
    return {
        type: 'click',
        defaultPrevented: false,
        stopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.stopped = true; },
    };
}

function harness() {
    const doc = createDoc();
    const trigger = doc.createElement('button');
    trigger.setAttribute('id', 'stop-all');
    trigger.setAttribute('type', 'button');
    trigger.textContent = 'Stop all stages';
    doc.body.appendChild(trigger);
    trigger.focus();
    const api = createConfirmDialog(doc);
    return { doc, trigger, api };
}

test('createConfirmDialog returns null without a document and reuses one controller', () => {
    assert.strictEqual(createConfirmDialog(null), null);
    const { doc, api } = harness();
    assert.strictEqual(createConfirmDialog(doc), api);
    const dialogs = doc.body.children.filter(el => el.id === 'cc-confirm-dialog');
    assert.strictEqual(dialogs.length, 1);
});

test('stop-all-stages opens an alertdialog, focuses Cancel, and hides the page', () => {
    const { doc, trigger, api } = harness();
    let calls = 0;
    assert.strictEqual(api.openAction('stop-all-stages', () => { calls++; }, trigger), true);
    assert.strictEqual(api.isOpen(), true);
    const dialog = doc.getElementById('cc-confirm-dialog');
    assert.strictEqual(dialog.getAttribute('role'), 'alertdialog');
    assert.strictEqual(dialog.getAttribute('aria-modal'), 'true');
    assert.strictEqual(dialog.hidden, false);
    assert.strictEqual(dialog.parentElement, doc.body, 'dialog is a body sibling so it is not hidden with the page');
    assert.strictEqual(doc.getElementById('cc-confirm-title').textContent, 'Stop all stages?');
    assert.match(doc.getElementById('cc-confirm-message').textContent, /In-flight work is dropped/);
    assert.strictEqual(doc.getElementById('cc-confirm-ok').textContent, 'Stop all stages');
    assert.strictEqual(doc.getElementById('cc-confirm-cancel').textContent, 'Cancel');
    assert.strictEqual(doc.activeElement, doc.getElementById('cc-confirm-cancel'), 'Cancel takes initial focus');
    assert.strictEqual(trigger.getAttribute('aria-hidden'), 'true');
    assert.strictEqual(trigger.inert, true);
    assert.notStrictEqual(dialog.getAttribute('aria-hidden'), 'true');
    assert.strictEqual(doc.body.style.overflow, 'hidden');
    assert.strictEqual(calls, 0);
});

test('Tab and Shift+Tab stay inside the dialog, and focus pulled outside is returned', () => {
    const { doc, trigger, api } = harness();
    const outside = doc.createElement('button');
    outside.setAttribute('id', 'outside');
    outside.setAttribute('type', 'button');
    doc.body.appendChild(outside);
    api.openAction('stop-all-stages', () => {}, trigger);
    const cancel = doc.getElementById('cc-confirm-cancel');
    const ok = doc.getElementById('cc-confirm-ok');
    assert.strictEqual(doc.activeElement, cancel);

    const tab = keyEvent('Tab');
    doc.dispatchEvent(tab);
    assert.strictEqual(tab.defaultPrevented, true);
    assert.strictEqual(doc.activeElement, ok);

    doc.dispatchEvent(keyEvent('Tab'));
    assert.strictEqual(doc.activeElement, cancel, 'Tab wraps to the first control');

    doc.dispatchEvent(keyEvent('Tab', { shiftKey: true }));
    assert.strictEqual(doc.activeElement, ok, 'Shift+Tab wraps to the last control');

    outside.focus();
    assert.strictEqual(doc.activeElement, ok, 'focus that leaves the dialog is pulled back');
    for (let i = 0; i < 5; i++) {
        doc.dispatchEvent(keyEvent('Tab', { shiftKey: i % 2 === 1 }));
        assert.ok(doc.activeElement === cancel || doc.activeElement === ok);
    }
    assert.strictEqual(api.isOpen(), true);
});

test('Esc and Cancel dismiss without the callback and return focus', () => {
    const { doc, trigger, api } = harness();
    let calls = 0;
    api.openAction('stop-all-stages', () => { calls++; }, trigger);
    const esc = keyEvent('Escape');
    doc.dispatchEvent(esc);
    assert.strictEqual(esc.defaultPrevented, true);
    assert.strictEqual(api.isOpen(), false);
    assert.strictEqual(calls, 0);
    assert.strictEqual(doc.activeElement, trigger);
    assert.strictEqual(trigger.hasAttribute('aria-hidden'), false);
    assert.strictEqual(trigger.inert, false);
    assert.strictEqual(doc.getElementById('cc-confirm-dialog').hidden, true);
    assert.strictEqual(doc.body.style.overflow, '');

    api.openAction('stop-all-stages', () => { calls++; }, trigger);
    doc.getElementById('cc-confirm-cancel').dispatchEvent(clickEvent());
    assert.strictEqual(api.isOpen(), false);
    assert.strictEqual(calls, 0);
    assert.strictEqual(doc.activeElement, trigger);

    api.openAction('stop-all-stages', () => { calls++; }, trigger);
    doc.dispatchEvent(keyEvent('Esc'));
    assert.strictEqual(calls, 0);
    assert.strictEqual(api.isOpen(), false);
});

test('Confirm runs the callback once, then a second click does nothing', () => {
    const { doc, trigger, api } = harness();
    let calls = 0;
    let focusDuring = null;
    api.openAction('stop-all-stages', () => {
        calls++;
        focusDuring = doc.activeElement;
        doc.getElementById('cc-confirm-ok').dispatchEvent(clickEvent());
    }, trigger);
    const ok = doc.getElementById('cc-confirm-ok');
    ok.dispatchEvent(clickEvent());
    assert.strictEqual(calls, 1);
    assert.strictEqual(focusDuring, trigger, 'focus returns before the callback');
    assert.strictEqual(doc.activeElement, trigger);
    assert.strictEqual(api.isOpen(), false);
    ok.dispatchEvent(clickEvent());
    assert.strictEqual(calls, 1);

    api.openAction('stop-all-stages', () => { calls++; }, trigger);
    doc.getElementById('cc-confirm-ok').dispatchEvent(clickEvent());
    assert.strictEqual(calls, 2, 'a later open can confirm again');
});

test('a thrown callback still dismisses and does not run twice', () => {
    const { doc, trigger, api } = harness();
    let calls = 0;
    api.openAction('stop-all-stages', () => {
        calls++;
        throw new Error('boom');
    }, trigger);
    assert.throws(() => doc.getElementById('cc-confirm-ok').dispatchEvent(clickEvent()), /boom/);
    assert.strictEqual(calls, 1);
    assert.strictEqual(api.isOpen(), false);
    assert.strictEqual(doc.activeElement, trigger);
    doc.getElementById('cc-confirm-ok').dispatchEvent(clickEvent());
    assert.strictEqual(calls, 1);
});

test('a second open and an unknown action do not replace the pending callback', () => {
    const { doc, trigger, api } = harness();
    let first = 0;
    let second = 0;
    assert.strictEqual(api.openAction('stop-all-stages', () => { first++; }, trigger), true);
    const message = doc.getElementById('cc-confirm-message').textContent;
    assert.strictEqual(api.openAction('stop-all-stages', () => { second++; }, trigger), false);
    assert.strictEqual(api.open({ title: 'Other', message: 'nope', onConfirm() { second++; } }), false);
    assert.strictEqual(doc.getElementById('cc-confirm-message').textContent, message);
    doc.getElementById('cc-confirm-ok').dispatchEvent(clickEvent());
    assert.strictEqual(first, 1);
    assert.strictEqual(second, 0);
    assert.strictEqual(api.openAction('not-an-action', () => { second++; }), false);
    assert.strictEqual(second, 0);
    assert.strictEqual(api.isOpen(), false);
});

test('dialog copy is text, backdrop clicks do not confirm, and markup is not parsed', () => {
    const { doc, trigger, api } = harness();
    let calls = 0;
    api.open({
        title: '<b>nope</b>',
        message: '<img src=x onerror=alert(1)>',
        confirmLabel: 'Do it',
        onConfirm() { calls++; },
        trigger: trigger,
    });
    const title = doc.getElementById('cc-confirm-title');
    const message = doc.getElementById('cc-confirm-message');
    assert.strictEqual(title.textContent, '<b>nope</b>');
    assert.strictEqual(message.textContent, '<img src=x onerror=alert(1)>');
    assert.strictEqual(title.children.length, 0);
    assert.strictEqual(message.children.length, 0);
    doc.getElementById('cc-confirm-dialog').children[0].dispatchEvent(clickEvent());
    assert.strictEqual(api.isOpen(), true);
    assert.strictEqual(calls, 0);
    assert.strictEqual(doc.getElementById('cc-confirm-ok').textContent, 'Do it');
});

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const modal = fs.readFileSync(path.join(__dirname, '..', 'modal.js'), 'utf8');
const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));

test('the page shells the dialog outside main and loads modal.js before app.js', () => {
    const body = html.slice(html.indexOf('<body'));
    const mainEnd = body.indexOf('</main>');
    const dialogAt = body.indexOf('id="cc-confirm-dialog"');
    assert.ok(mainEnd > 0 && dialogAt > mainEnd, 'dialog follows </main>');
    assert.match(body, /id="cc-confirm-dialog"[^>]*role="alertdialog"[^>]*aria-modal="true"[^>]*hidden/);
    assert.match(body, /id="cc-confirm-title"/);
    assert.match(body, /id="cc-confirm-message"/);
    assert.match(body, /id="cc-confirm-cancel"/);
    assert.match(body, /id="cc-confirm-ok"/);
    for (const id of ['cc-confirm-dialog', 'cc-confirm-title', 'cc-confirm-message', 'cc-confirm-cancel', 'cc-confirm-ok']) {
        assert.match(modal, new RegExp(id));
    }
    const scripts = [...html.matchAll(/<script src="([^"]+)"/g)].map(m => m[1]);
    assert.ok(scripts.indexOf('modal.js') !== -1 && scripts.indexOf('modal.js') < scripts.indexOf('app.js'));
    assert.match(app, /installDestructiveConfirm\(\)/);
    assert.match(app, /confirmDestructiveJobAction = function \(action, onConfirm, trigger\)/);
    assert.match(app, /openAction\(action, onConfirm, trigger\)/);
    assert.match(app, /getElementById\('cc-confirm-dialog'\)/);
    assert.match(app, /if \(confirmDialog && !confirmDialog\.hidden\) return/);
    assert.ok(!/innerHTML|insertAdjacentHTML|outerHTML/.test(modal));
});

function lum(hex) {
    let h = hex.replace('#', '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    const c = h.match(/../g).map(part => parseInt(part, 16) / 255)
        .map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function ratio(a, b) {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
}

test('danger confirm text meets WCAG AA and the dialog hides with [hidden]', () => {
    const danger = css.match(/\.cc-modal-btn-danger\s*\{([^}]*)\}/)[1];
    const bg = danger.match(/(?:^|[^-])background:\s*(#[0-9a-fA-F]{3,6})/)[1];
    const fg = danger.match(/(?:^|[^-])color:\s*(#[0-9a-fA-F]{3,6})/)[1];
    assert.ok(ratio(fg, bg) >= 4.5, `${fg} on ${bg} = ${ratio(fg, bg)}`);
    assert.match(css, /\.cc-modal\[hidden\]\s*\{[^}]*display:\s*none\s*!important/);
    assert.match(css, /@media \(forced-colors: active\)[\s\S]*\.cc-modal-panel[\s\S]*border:\s*2px solid CanvasText/);
    assert.match(css, /@media \(pointer: coarse\)\s*\{[^}]*\.cc-modal-btn\s*\{[^}]*min-height:\s*44px/);
});
