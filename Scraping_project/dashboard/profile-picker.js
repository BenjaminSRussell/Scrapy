// Site profile picker (#1001). Mock list until /api/profiles exists.
// Does not fetch and does not start a crawl (#147 stays the job API).
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else Object.assign(root, api);
})(typeof self !== 'undefined' ? self : this, function () {
    const STORAGE_KEY = 'cc.siteProfile';
    const API_PROFILES_PATH = '/api/profiles';
    // Stand-in crawl profiles. Not a live target list.
    const MOCK_SITE_PROFILES = Object.freeze([
        Object.freeze({ id: 'uconn', name: 'UConn', domains: Object.freeze(['uconn.edu']) }),
        Object.freeze({ id: 'uconn-today', name: 'UConn Today', domains: Object.freeze(['today.uconn.edu']) }),
        Object.freeze({ id: 'sandbox', name: 'Sandbox', domains: Object.freeze(['example.test']) }),
    ]);
    const ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
    const DOMAIN_RE = /^[a-z0-9.-]{1,253}$/;

    function cleanName(name) {
        return String(name == null ? '' : name)
            .replace(/[\u0000-\u001f\u007f]/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 80);
    }

    function cleanDomain(domain) {
        const s = String(domain == null ? '' : domain).trim().toLowerCase();
        if (!DOMAIN_RE.test(s) || s.indexOf('..') !== -1) return '';
        return s;
    }

    function normalizeProfile(raw) {
        if (!raw || typeof raw !== 'object') return null;
        const id = String(raw.id == null ? '' : raw.id).trim();
        const name = cleanName(raw.name);
        if (!ID_RE.test(id) || !name) return null;
        const domains = [];
        const rawDomains = Array.isArray(raw.domains) ? raw.domains : [];
        for (let i = 0; i < rawDomains.length && domains.length < 8; i++) {
            const d = cleanDomain(rawDomains[i]);
            if (d && domains.indexOf(d) === -1) domains.push(d);
        }
        return { id: id, name: name, domains: domains };
    }

    function profilesFromSource(list, source) {
        const src = source === 'api' ? 'api' : 'mock';
        const raw = (Array.isArray(list) && list.length) ? list : (src === 'mock' ? MOCK_SITE_PROFILES : []);
        const profiles = [];
        const seen = new Set();
        for (let i = 0; i < raw.length; i++) {
            const profile = normalizeProfile(raw[i]);
            if (!profile || seen.has(profile.id)) continue;
            seen.add(profile.id);
            profiles.push(profile);
        }
        return { source: src, profiles: profiles, badge: src === 'mock' ? 'mock' : '' };
    }

    function optionLabel(profile, source) {
        if (!profile) return '';
        return source === 'mock' ? profile.name + ' (mock)' : profile.name;
    }

    function initialState(list, source, selectedId) {
        const model = profilesFromSource(list, source);
        const ids = model.profiles.map(function (p) { return p.id; });
        let selected = '';
        if (selectedId && ids.indexOf(String(selectedId)) !== -1) selected = String(selectedId);
        else if (ids.length) selected = ids[0];
        const activeIndex = selected ? ids.indexOf(selected) : 0;
        return {
            source: model.source,
            badge: model.badge,
            profiles: model.profiles,
            selectedId: selected,
            open: false,
            activeIndex: activeIndex < 0 ? 0 : activeIndex,
        };
    }

    function selectedProfile(state) {
        if (!state || !state.profiles) return null;
        for (let i = 0; i < state.profiles.length; i++) {
            if (state.profiles[i].id === state.selectedId) return state.profiles[i];
        }
        return null;
    }

    function announceSelection(state) {
        const profile = selectedProfile(state);
        if (!profile) return 'No crawl profile selected';
        const mock = state && state.source === 'mock' ? ' Mock list.' : '';
        return 'Crawl profile ' + profile.name + '.' + mock;
    }

    function withPatch(state, patch) {
        return {
            source: state.source,
            badge: state.badge,
            profiles: state.profiles,
            selectedId: patch.selectedId != null ? patch.selectedId : state.selectedId,
            open: patch.open != null ? patch.open : state.open,
            activeIndex: patch.activeIndex != null ? patch.activeIndex : state.activeIndex,
        };
    }

    // Select-only combobox keys. null means the event is not handled.
    // Arrows, Home, and End move the active option. Enter and Space commit.
    // Escape and Tab close without changing the selection.
    function profilePickerKey(state, key) {
        if (!state || !state.profiles || !state.profiles.length) return null;
        const n = state.profiles.length;
        const selectedIndex = Math.max(0, state.profiles.findIndex(function (p) {
            return p.id === state.selectedId;
        }));
        const active = state.open
            ? Math.min(Math.max(state.activeIndex || 0, 0), n - 1)
            : selectedIndex;
        if (key === 'ArrowDown') {
            return withPatch(state, {
                open: true,
                activeIndex: state.open ? Math.min(n - 1, active + 1) : active,
            });
        }
        if (key === 'ArrowUp') {
            return withPatch(state, {
                open: true,
                activeIndex: state.open ? Math.max(0, active - 1) : active,
            });
        }
        if (key === 'Home') return withPatch(state, { open: true, activeIndex: 0 });
        if (key === 'End') return withPatch(state, { open: true, activeIndex: n - 1 });
        if (key === 'Enter' || key === ' ') {
            if (!state.open) return withPatch(state, { open: true, activeIndex: active });
            return withPatch(state, {
                open: false,
                selectedId: state.profiles[active].id,
                activeIndex: active,
            });
        }
        if (key === 'Escape' || key === 'Tab') {
            if (!state.open) return null;
            return withPatch(state, { open: false, activeIndex: selectedIndex });
        }
        return null;
    }

    function optionNode(target, list) {
        let node = target;
        while (node && node !== list) {
            if (node.getAttribute && node.getAttribute('data-id')) return node;
            node = node.parentNode;
        }
        return null;
    }

    function mountProfilePicker(doc, opts) {
        const documentRef = (doc && typeof doc.getElementById === 'function')
            ? doc
            : (typeof document !== 'undefined' ? document : null);
        if (!documentRef) return null;
        const rootEl = documentRef.getElementById('site-profile-picker');
        const input = documentRef.getElementById('site-profile-input');
        const list = documentRef.getElementById('site-profile-listbox');
        const badge = documentRef.getElementById('site-profile-badge');
        const status = documentRef.getElementById('site-profile-status');
        const toggle = documentRef.getElementById('site-profile-toggle');
        const hint = documentRef.getElementById('site-profile-hint');
        if (!rootEl || !input || !list) return null;

        const options = opts || {};
        let storage = options.storage;
        if (storage === undefined) {
            try { storage = (typeof localStorage !== 'undefined') ? localStorage : null; }
            catch (err) { storage = null; }
        }
        function readStored() {
            try { return storage ? storage.getItem(STORAGE_KEY) : ''; }
            catch (err) { return ''; }
        }
        function writeStored(id) {
            try { if (storage) storage.setItem(STORAGE_KEY, id); }
            catch (err) { /* privacy mode or file:// */ }
        }

        let state = initialState(options.profiles || null, options.source || 'mock', options.selectedId || readStored());

        function render() {
            while (list.firstChild) list.removeChild(list.firstChild);
            for (let i = 0; i < state.profiles.length; i++) {
                const profile = state.profiles[i];
                const li = documentRef.createElement('li');
                li.id = 'site-profile-opt-' + profile.id;
                li.setAttribute('role', 'option');
                li.setAttribute('data-id', profile.id);
                li.setAttribute('aria-selected', profile.id === state.selectedId ? 'true' : 'false');
                li.className = 'profile-picker-option'
                    + (state.open && i === state.activeIndex ? ' is-active' : '');
                const name = documentRef.createElement('span');
                name.className = 'profile-picker-option-name';
                name.textContent = profile.name;
                li.appendChild(name);
                if (state.source === 'mock') {
                    const mark = documentRef.createElement('span');
                    mark.className = 'card-badge badge-warning profile-picker-option-badge';
                    mark.textContent = 'mock';
                    li.appendChild(mark);
                }
                if (profile.domains.length) {
                    const domains = documentRef.createElement('span');
                    domains.className = 'profile-picker-domains';
                    domains.textContent = profile.domains.join(', ');
                    li.appendChild(domains);
                }
                list.appendChild(li);
            }
            const current = selectedProfile(state);
            input.value = optionLabel(current, state.source);
            input.setAttribute('aria-expanded', state.open ? 'true' : 'false');
            if (state.open && state.profiles[state.activeIndex]) {
                input.setAttribute('aria-activedescendant', 'site-profile-opt-' + state.profiles[state.activeIndex].id);
            } else {
                input.removeAttribute('aria-activedescendant');
            }
            list.hidden = !state.open;
            if (toggle) {
                toggle.setAttribute('aria-expanded', state.open ? 'true' : 'false');
                toggle.setAttribute('aria-label', state.open ? 'Hide crawl profiles' : 'Show crawl profiles');
            }
            if (badge) {
                badge.hidden = !state.badge;
                badge.textContent = state.badge || '';
            }
            if (hint) {
                hint.textContent = state.source === 'mock'
                    ? 'Mock profiles until ' + API_PROFILES_PATH + ' is available. Choosing a profile does not start a crawl.'
                    : 'Profiles loaded from ' + API_PROFILES_PATH + '. Choosing a profile does not start a crawl.';
            }
            rootEl.dataset.profileId = state.selectedId;
            rootEl.dataset.profileSource = state.source;
        }

        function commit(next, announce) {
            if (!next) return;
            const prevId = state.selectedId;
            state = next;
            render();
            if (state.selectedId !== prevId) writeStored(state.selectedId);
            if (announce && status && state.selectedId !== prevId) {
                status.textContent = announceSelection(state);
            }
        }

        function onInputKey(event) {
            const next = profilePickerKey(state, event.key);
            if (!next) return;
            if (event.key !== 'Tab') event.preventDefault();
            const committed = (event.key === 'Enter' || event.key === ' ') && !next.open;
            commit(next, committed);
        }

        input.addEventListener('keydown', onInputKey);
        input.addEventListener('click', function () {
            const idx = Math.max(0, state.profiles.findIndex(function (p) { return p.id === state.selectedId; }));
            commit(withPatch(state, { open: !state.open, activeIndex: state.open ? state.activeIndex : idx }), false);
        });
        if (toggle) {
            toggle.addEventListener('click', function () {
                const idx = Math.max(0, state.profiles.findIndex(function (p) { return p.id === state.selectedId; }));
                const opening = !state.open;
                commit(withPatch(state, { open: opening, activeIndex: opening ? idx : state.activeIndex }), false);
                if (opening) input.focus();
            });
            toggle.addEventListener('keydown', function (event) {
                if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Home' && event.key !== 'End' && event.key !== 'Escape') {
                    return;
                }
                event.preventDefault();
                input.focus();
                const next = profilePickerKey(state, event.key);
                if (next) commit(next, false);
            });
        }
        list.addEventListener('click', function (event) {
            const li = optionNode(event.target, list);
            if (!li) return;
            const id = li.getAttribute('data-id');
            const idx = state.profiles.findIndex(function (p) { return p.id === id; });
            if (idx < 0) return;
            commit(withPatch(state, { open: false, selectedId: id, activeIndex: idx }), true);
        });
        documentRef.addEventListener('click', function (event) {
            if (!state.open) return;
            if (rootEl.contains && rootEl.contains(event.target)) return;
            let node = event.target;
            while (node) {
                if (node === rootEl) return;
                node = node.parentNode;
            }
            commit(withPatch(state, { open: false }), false);
        });

        render();
        if (status) status.textContent = announceSelection(state);
        return {
            getState: function () { return state; },
        };
    }

    return {
        STORAGE_KEY: STORAGE_KEY,
        API_PROFILES_PATH: API_PROFILES_PATH,
        MOCK_SITE_PROFILES: MOCK_SITE_PROFILES,
        normalizeProfile: normalizeProfile,
        profilesFromSource: profilesFromSource,
        optionLabel: optionLabel,
        initialState: initialState,
        selectedProfile: selectedProfile,
        announceSelection: announceSelection,
        profilePickerKey: profilePickerKey,
        mountProfilePicker: mountProfilePicker,
    };
});
