// Control Center string catalogs (#955). English is the fallback.
// es is a stub: same keys, unreviewed copy, empty values fall back to en.
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.CCI18n = api;
    if (typeof document !== 'undefined' && document.getElementById && document.getElementById('cc-lang')) {
        api.mount(document);
    }
})(typeof self !== 'undefined' ? self : this, function () {
    const DEFAULT_LOCALE = 'en';
    const SUPPORTED = ['en', 'es'];
    const STORAGE_KEY = 'cc.locale';

    function messagesOf(catalog) {
        if (!catalog || typeof catalog !== 'object') return {};
        if (catalog.messages && typeof catalog.messages === 'object') return catalog.messages;
        return catalog;
    }

    function createI18n(catalogs, initial) {
        const cats = catalogs || {};
        let locale = SUPPORTED.indexOf(initial) >= 0 && cats[initial] ? initial : DEFAULT_LOCALE;

        function lookup(code, key) {
            const msgs = messagesOf(cats[code]);
            if (msgs && Object.prototype.hasOwnProperty.call(msgs, key)) {
                const value = msgs[key];
                if (typeof value === 'string' && value !== '') return value;
            }
            return null;
        }

        function t(key, vars) {
            if (typeof key !== 'string' || key === '') return '';
            let text = lookup(locale, key);
            if (text == null && locale !== DEFAULT_LOCALE) text = lookup(DEFAULT_LOCALE, key);
            if (text == null) return key;
            if (vars && typeof vars === 'object') {
                text = text.replace(/\{(\w+)\}/g, function (match, name) {
                    if (Object.prototype.hasOwnProperty.call(vars, name) && vars[name] != null) return String(vars[name]);
                    return match;
                });
            }
            return text;
        }

        function getLocale() { return locale; }

        function setLocale(code) {
            if (SUPPORTED.indexOf(code) < 0 || !cats[code]) return locale;
            locale = code;
            return locale;
        }

        return { t: t, getLocale: getLocale, setLocale: setLocale };
    }

    // Keep element children (icons, counts). Replace the first text node only.
    function replaceOwnText(el, value) {
        const nodes = el.childNodes ? Array.prototype.slice.call(el.childNodes) : [];
        const elements = nodes.filter(function (n) { return n.nodeType === 1; });
        if (!elements.length) {
            el.textContent = value;
            return;
        }
        const texts = nodes.filter(function (n) { return n.nodeType === 3 && String(n.textContent).trim(); });
        if (!texts.length) return;
        const node = texts[0];
        const raw = String(node.textContent);
        const lead = (/^\s*/.exec(raw) || [''])[0];
        const trail = (/\s*$/.exec(raw) || [''])[0];
        node.textContent = lead + value + trail;
    }

    function apply(doc, translate) {
        if (!doc || !doc.querySelectorAll || typeof translate !== 'function') return;
        function each(selector, fn) {
            Array.prototype.forEach.call(doc.querySelectorAll(selector), fn);
        }
        each('[data-i18n]', function (el) {
            if (el.getAttribute('data-i18n-owned') === 'js') return;
            replaceOwnText(el, translate(el.getAttribute('data-i18n')));
        });
        each('[data-i18n-aria-label]', function (el) {
            if (el.getAttribute('data-i18n-owned') === 'js') return;
            el.setAttribute('aria-label', translate(el.getAttribute('data-i18n-aria-label')));
        });
        each('[data-i18n-title]', function (el) {
            if (el.getAttribute('data-i18n-owned') === 'js') return;
            el.setAttribute('title', translate(el.getAttribute('data-i18n-title')));
        });
        each('[data-i18n-placeholder]', function (el) {
            if (el.getAttribute('data-i18n-owned') === 'js') return;
            el.setAttribute('placeholder', translate(el.getAttribute('data-i18n-placeholder')));
        });
    }

    function readStoredLocale(storage, search) {
        let fromQuery = '';
        try {
            fromQuery = new URLSearchParams(String(search || '')).get('lang') || '';
        } catch (_) { fromQuery = ''; }
        if (SUPPORTED.indexOf(fromQuery) >= 0) return fromQuery;
        let stored = '';
        try { stored = storage && storage.getItem ? (storage.getItem(STORAGE_KEY) || '') : ''; }
        catch (_) { stored = ''; }
        if (SUPPORTED.indexOf(stored) >= 0) return stored;
        return DEFAULT_LOCALE;
    }

    function writeStoredLocale(storage, code) {
        try { if (storage && storage.setItem) storage.setItem(STORAGE_KEY, code); }
        catch (_) { /* privacy mode or file:// */ }
    }

    const api = {
        DEFAULT_LOCALE: DEFAULT_LOCALE,
        SUPPORTED: SUPPORTED,
        STORAGE_KEY: STORAGE_KEY,
        _ready: false,
        createI18n: createI18n,
        apply: apply,
        replaceOwnText: replaceOwnText,
        readStoredLocale: readStoredLocale,
        writeStoredLocale: writeStoredLocale,
        messagesOf: messagesOf,
        t: function (key) { return key; },
        getLocale: function () { return DEFAULT_LOCALE; },
        setLocale: function () { return DEFAULT_LOCALE; },
        mount: function (doc, opts) {
            const documentRef = doc || (typeof document !== 'undefined' ? document : null);
            if (!documentRef || !documentRef.getElementById) return Promise.resolve(null);
            const o = opts || {};
            const storage = o.storage !== undefined ? o.storage : (typeof localStorage !== 'undefined' ? localStorage : null);
            const search = o.search != null ? o.search : (typeof location !== 'undefined' ? location.search : '');
            const fetchFn = o.fetch || (typeof fetch === 'function' ? fetch : null);
            const base = o.base || 'locales/';
            let wanted = readStoredLocale(storage, search);
            const sel = documentRef.getElementById('cc-lang');
            if (sel && !sel.getAttribute('data-i18n-bound')) {
                sel.setAttribute('data-i18n-bound', '1');
                sel.addEventListener('change', function () {
                    wanted = sel.value;
                    if (api._i18n) api.setLocale(sel.value);
                });
            }

            function load(code) {
                if (o.catalogs && o.catalogs[code]) return Promise.resolve(o.catalogs[code]);
                if (!fetchFn) return Promise.reject(new Error('no fetch'));
                return fetchFn(base + code + '.json', { cache: 'force-cache' }).then(function (resp) {
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    return resp.json();
                });
            }

            const codes = SUPPORTED.slice();
            return Promise.all(codes.map(function (code) {
                return load(code).then(function (cat) { return [code, cat]; }, function () { return [code, null]; });
            })).then(function (pairs) {
                const catalogs = {};
                pairs.forEach(function (pair) { if (pair[1]) catalogs[pair[0]] = pair[1]; });
                if (!catalogs[DEFAULT_LOCALE]) return null;
                if (sel && SUPPORTED.indexOf(sel.value) >= 0 && sel.value !== wanted && catalogs[sel.value]) wanted = sel.value;
                const i18n = createI18n(catalogs, catalogs[wanted] ? wanted : DEFAULT_LOCALE);
                api._i18n = i18n;
                api.t = function (key, vars) { return i18n.t(key, vars); };
                api.getLocale = function () { return i18n.getLocale(); };
                function paint() {
                    apply(documentRef, api.t);
                    if (documentRef.documentElement) documentRef.documentElement.lang = i18n.getLocale();
                    const picker = documentRef.getElementById('cc-lang');
                    if (picker && picker.value !== i18n.getLocale()) picker.value = i18n.getLocale();
                    writeStoredLocale(storage, i18n.getLocale());
                    api._ready = true;
                    if (documentRef.dispatchEvent && typeof CustomEvent === 'function') {
                        documentRef.dispatchEvent(new CustomEvent('cc:locale', { detail: { locale: i18n.getLocale() } }));
                    }
                }
                api.setLocale = function (code) {
                    const next = i18n.setLocale(code);
                    paint();
                    return next;
                };
                paint();
                return i18n;
            });
        },
    };

    return api;
});
