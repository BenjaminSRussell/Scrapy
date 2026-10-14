// English string catalog (#1006). Default locale is en. A missing key returns
// the key name outside production so a forgotten string is visible in dev.
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else Object.assign(root, api);
})(typeof self !== 'undefined' ? self : this, function () {
    const catalogs = Object.create(null);
    let locale = 'en';

    function registerCatalog(code, messages) {
        if (!code || !messages || typeof messages !== 'object') return;
        catalogs[String(code)] = messages;
    }

    function setLocale(code) {
        locale = code || 'en';
    }

    // local / unset is dev. Anything matching /prod/i (production, prod) is not.
    function devFallback(env) {
        const value = env == null ? '' : String(env);
        if (!value) return true;
        return !/prod/i.test(value);
    }

    function runtimeEnv() {
        const w = typeof window !== 'undefined' ? window : null;
        if (!w) return 'local';
        const features = w.__CC_FEATURES__;
        return (features && features.env) || w.__CC_ENV__ || 'local';
    }

    function lookup(key) {
        const active = catalogs[locale];
        if (active && Object.prototype.hasOwnProperty.call(active, key) && active[key] != null && active[key] !== '') {
            return String(active[key]);
        }
        const en = catalogs.en;
        if (locale !== 'en' && en && Object.prototype.hasOwnProperty.call(en, key) && en[key] != null && en[key] !== '') {
            return String(en[key]);
        }
        return null;
    }

    function interpolate(template, vars) {
        if (!vars || typeof vars !== 'object') return template;
        return template.replace(/\{(\w+)\}/g, (all, name) => (
            Object.prototype.hasOwnProperty.call(vars, name) && vars[name] != null ? String(vars[name]) : all
        ));
    }

    // t(key, vars[, env]). env is optional and overrides the runtime for tests;
    // the page calls t(key) or t(key, vars).
    function t(key, vars, env) {
        const name = String(key);
        const found = lookup(name);
        if (found == null) {
            const mode = arguments.length > 2 ? env : runtimeEnv();
            return devFallback(mode) ? name : '';
        }
        return interpolate(found, vars);
    }

    // Keep a leading aria-hidden span (tab emoji) and replace only the label.
    function writeText(el, value) {
        const nodes = el.childNodes;
        if (nodes && nodes.length) {
            const keep = [];
            for (let i = 0; i < nodes.length; i++) {
                const n = nodes[i];
                if (n.nodeType === 1 && n.getAttribute && n.getAttribute('aria-hidden') === 'true') keep.push(n);
            }
            if (keep.length && typeof el.removeChild === 'function' && typeof el.appendChild === 'function') {
                while (el.firstChild) el.removeChild(el.firstChild);
                keep.forEach((n) => el.appendChild(n));
                const doc = el.ownerDocument || (typeof document !== 'undefined' ? document : null);
                const text = doc && doc.createTextNode ? doc.createTextNode(' ' + value) : null;
                if (text) el.appendChild(text);
                return;
            }
        }
        el.textContent = value;
    }

    // [data-i18n] sets text. [data-i18n-attr] writes that attribute instead,
    // so a container (topbar title, tablist) keeps its children.
    // [data-i18n-aria] sets aria-label from a second key.
    function applyI18n(doc) {
        const scope = doc || (typeof document !== 'undefined' ? document : null);
        if (!scope || typeof scope.querySelectorAll !== 'function') return;
        scope.querySelectorAll('[data-i18n]').forEach((el) => {
            const key = el.getAttribute('data-i18n');
            if (!key) return;
            const attr = el.getAttribute('data-i18n-attr');
            if (attr) el.setAttribute(attr, t(key));
            else writeText(el, t(key));
            const ariaKey = el.getAttribute('data-i18n-aria');
            if (ariaKey) el.setAttribute('aria-label', t(ariaKey));
        });
        // The nav opening tag is matched exactly by the a11y test, so it has
        // no data-i18n attribute. Its landmark name still comes from the catalog.
        if (typeof scope.querySelector === 'function') {
            const nav = scope.querySelector('nav.tab-buttons-wrap');
            if (nav && nav.setAttribute) nav.setAttribute('aria-label', t('tabs.nav'));
        }
    }

    function hasCatalog() {
        const active = catalogs[locale];
        if (active && Object.keys(active).length > 0) return true;
        return !!(locale !== 'en' && catalogs.en && Object.keys(catalogs.en).length > 0);
    }

    function loadCatalog(url, code) {
        if (typeof fetch !== 'function') return Promise.reject(new Error('fetch unavailable'));
        const target = url || 'strings/en.json';
        const loc = code || 'en';
        return fetch(target, { credentials: 'same-origin' }).then((resp) => {
            if (!resp.ok) throw new Error('catalog ' + resp.status);
            return resp.json();
        }).then((messages) => {
            registerCatalog(loc, messages);
            return messages;
        });
    }

    return { t, registerCatalog, setLocale, applyI18n, loadCatalog, hasCatalog, devFallback };
});
