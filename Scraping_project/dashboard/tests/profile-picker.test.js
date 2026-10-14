'use strict';
// #1001: keyboard-operable site profile combobox. Mock list until /api/profiles.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const picker = require('../profile-picker.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const src = fs.readFileSync(path.join(__dirname, '..', 'profile-picker.js'), 'utf8');
const body = html.slice(html.indexOf('<body'));

test('markup is a labelled combobox with a visible mock badge (#1001)', () => {
    assert.match(body, /id="site-profile-picker"/);
    assert.match(body, /<label id="site-profile-label" for="site-profile-input">Crawl profile<\/label>/);
    assert.match(body, /id="site-profile-input"[^>]*role="combobox"/);
    assert.match(body, /aria-expanded="false"/);
    assert.match(body, /aria-controls="site-profile-listbox"/);
    assert.match(body, /id="site-profile-listbox"[^>]*role="listbox"/);
    assert.match(body, /id="site-profile-badge"[^>]*>mock</);
    assert.match(body, /\/api\/profiles/);
    assert.match(body, /id="site-profile-status"[^>]*role="status"/);
    assert.match(body, /aria-live="polite"/);
    assert.match(html, /<script src="profile-picker.js"><\/script>\s*<script src="app.js"><\/script>/);
    assert.match(app, /mountProfilePicker\(document\)/);
    const first = picker.MOCK_SITE_PROFILES[0];
    assert.match(body, new RegExp('value="' + picker.optionLabel(first, 'mock').replace(/[()]/g, '\\$&') + '"'));
});

test('picker script does not fetch or write markup with innerHTML', () => {
    assert.doesNotMatch(src, /fetch\s*\(/);
    assert.doesNotMatch(src, /innerHTML/);
    assert.strictEqual(picker.API_PROFILES_PATH, '/api/profiles');
});

test('mock profiles carry a mock badge and api profiles do not', () => {
    const mock = picker.profilesFromSource(null, 'mock');
    assert.strictEqual(mock.source, 'mock');
    assert.strictEqual(mock.badge, 'mock');
    assert.ok(mock.profiles.length >= 2);
    for (const profile of mock.profiles) {
        assert.match(picker.optionLabel(profile, 'mock'), /\(mock\)$/);
    }
    const api = picker.profilesFromSource(
        [{ id: 'uconn', name: 'UConn', domains: ['uconn.edu'] }],
        'api',
    );
    assert.strictEqual(api.source, 'api');
    assert.strictEqual(api.badge, '');
    assert.strictEqual(api.profiles.length, 1);
    assert.strictEqual(picker.optionLabel(api.profiles[0], 'api'), 'UConn');
    const emptyApi = picker.profilesFromSource(null, 'api');
    assert.deepStrictEqual(emptyApi.profiles, []);
    assert.strictEqual(emptyApi.badge, '');
});

test('arrow keys open and move, Enter and Space select, Escape closes (#1001)', () => {
    let state = picker.initialState(null, 'mock');
    assert.strictEqual(state.open, false);
    assert.strictEqual(state.selectedId, 'uconn');
    assert.strictEqual(state.badge, 'mock');

    state = picker.profilePickerKey(state, 'ArrowDown');
    assert.strictEqual(state.open, true);
    assert.strictEqual(state.activeIndex, 0);
    assert.strictEqual(state.selectedId, 'uconn');

    state = picker.profilePickerKey(state, 'ArrowDown');
    assert.strictEqual(state.activeIndex, 1);
    assert.strictEqual(state.selectedId, 'uconn', 'arrows move the active option only');

    state = picker.profilePickerKey(state, 'Enter');
    assert.strictEqual(state.open, false);
    assert.strictEqual(state.selectedId, 'uconn-today');

    state = picker.profilePickerKey(state, 'End');
    assert.strictEqual(state.open, true);
    assert.strictEqual(state.activeIndex, state.profiles.length - 1);
    const atEnd = state;
    state = picker.profilePickerKey(state, 'ArrowDown');
    assert.strictEqual(state.activeIndex, atEnd.profiles.length - 1, 'does not wrap past the end');

    state = picker.profilePickerKey(state, 'Escape');
    assert.strictEqual(state.open, false);
    assert.strictEqual(state.selectedId, 'uconn-today');
    assert.strictEqual(picker.profilePickerKey(state, 'Escape'), null);
    assert.strictEqual(picker.profilePickerKey(state, 'Tab'), null);
    assert.strictEqual(picker.profilePickerKey(state, 'x'), null);

    state = picker.profilePickerKey(state, 'Home');
    assert.strictEqual(state.open, true);
    assert.strictEqual(state.activeIndex, 0);
    state = picker.profilePickerKey(state, 'ArrowUp');
    assert.strictEqual(state.activeIndex, 0, 'does not wrap past the start');
    state = picker.profilePickerKey(state, ' ');
    assert.strictEqual(state.open, false);
    assert.strictEqual(state.selectedId, 'uconn');

    state = picker.profilePickerKey(state, 'ArrowUp');
    assert.strictEqual(state.open, true);
    state = picker.profilePickerKey(state, 'Tab');
    assert.strictEqual(state.open, false);
    assert.strictEqual(state.selectedId, 'uconn');
});

test('stored id is kept only when it is still in the list', () => {
    const kept = picker.initialState(null, 'mock', 'sandbox');
    assert.strictEqual(kept.selectedId, 'sandbox');
    assert.strictEqual(kept.activeIndex, 2);
    const dropped = picker.initialState(null, 'mock', '../not-a-profile');
    assert.strictEqual(dropped.selectedId, 'uconn');
});

test('rejects blank and unsafe profile ids', () => {
    assert.strictEqual(picker.normalizeProfile(null), null);
    assert.strictEqual(picker.normalizeProfile({ id: '', name: 'X' }), null);
    assert.strictEqual(picker.normalizeProfile({ id: '../etc', name: 'X' }), null);
    assert.strictEqual(picker.normalizeProfile({ id: 'Ok', name: 'X' }), null);
    assert.strictEqual(picker.normalizeProfile({ id: 'ok', name: '  ' }), null);
    const got = picker.normalizeProfile({
        id: 'uconn',
        name: 'UConn\n<script>',
        domains: ['uconn.edu', 'not a host', '..', 'uconn.edu'],
    });
    assert.strictEqual(got.id, 'uconn');
    assert.strictEqual(got.name, 'UConn <script>');
    assert.deepStrictEqual(got.domains, ['uconn.edu']);
});

function el(id) {
    return {
        id: id,
        hidden: false,
        value: '',
        className: '',
        textContent: '',
        dataset: {},
        children: [],
        parentNode: null,
        attrs: {},
        listeners: {},
        setAttribute(k, v) { this.attrs[k] = String(v); },
        getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
        removeAttribute(k) { delete this.attrs[k]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        removeChild(child) {
            const i = this.children.indexOf(child);
            if (i >= 0) this.children.splice(i, 1);
            child.parentNode = null;
            return child;
        },
        get firstChild() { return this.children[0] || null; },
        addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
        focus() { this.focused = true; },
        contains(node) {
            let n = node;
            while (n) {
                if (n === this) return true;
                n = n.parentNode;
            }
            return false;
        },
    };
}

test('mount renders each mock profile with a mock badge and keyboard selection', () => {
    const ids = [
        'site-profile-picker', 'site-profile-input', 'site-profile-listbox',
        'site-profile-badge', 'site-profile-status', 'site-profile-toggle', 'site-profile-hint',
    ];
    const nodes = {};
    ids.forEach((id) => { nodes[id] = el(id); });
    const doc = el('document');
    const store = new Map();
    doc.getElementById = (id) => nodes[id] || null;
    doc.createElement = () => el('el');
    doc.addEventListener = (type, fn) => { (doc.listeners[type] = doc.listeners[type] || []).push(fn); };
    const mounted = picker.mountProfilePicker(doc, {
        storage: {
            getItem: (k) => (store.has(k) ? store.get(k) : null),
            setItem: (k, v) => { store.set(k, v); },
        },
    });
    assert.ok(mounted);
    const list = nodes['site-profile-listbox'];
    assert.strictEqual(list.children.length, picker.MOCK_SITE_PROFILES.length);
    for (const option of list.children) {
        assert.strictEqual(option.attrs.role, 'option');
        const badge = option.children.find((c) => c.className.indexOf('profile-picker-option-badge') !== -1);
        assert.ok(badge, 'each mock option has a badge');
        assert.strictEqual(badge.textContent, 'mock');
    }
    assert.strictEqual(nodes['site-profile-badge'].textContent, 'mock');
    assert.strictEqual(nodes['site-profile-badge'].hidden, false);
    assert.strictEqual(nodes['site-profile-input'].value, 'UConn (mock)');
    assert.match(nodes['site-profile-status'].textContent, /Mock list/);
    assert.strictEqual(nodes['site-profile-picker'].dataset.profileSource, 'mock');

    const input = nodes['site-profile-input'];
    input.listeners.keydown.forEach((fn) => fn({ key: 'ArrowDown', preventDefault() {} }));
    input.listeners.keydown.forEach((fn) => fn({ key: 'ArrowDown', preventDefault() {} }));
    input.listeners.keydown.forEach((fn) => fn({ key: 'Enter', preventDefault() {} }));
    assert.strictEqual(mounted.getState().selectedId, 'uconn-today');
    assert.strictEqual(mounted.getState().open, false);
    assert.strictEqual(store.get(picker.STORAGE_KEY), 'uconn-today');
    assert.match(nodes['site-profile-status'].textContent, /UConn Today/);
    assert.strictEqual(list.hidden, true);
    const selected = list.children.find((c) => c.attrs['aria-selected'] === 'true');
    assert.strictEqual(selected.attrs['data-id'], 'uconn-today');
});

test('announce names the profile and says the list is mock', () => {
    const state = picker.initialState(null, 'mock');
    assert.match(picker.announceSelection(state), /UConn/);
    assert.match(picker.announceSelection(state), /Mock list/);
    const api = picker.initialState([{ id: 'uconn', name: 'UConn' }], 'api');
    assert.doesNotMatch(picker.announceSelection(api), /Mock/);
    assert.match(picker.announceSelection(picker.initialState([], 'api')), /No crawl profile/);
});
