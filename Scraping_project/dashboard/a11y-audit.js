// Accessibility audit mode (#950). Built-in checks always run. axe-core is
// optional: the toggle loads a pinned build from jsDelivr, and a failed or
// blocked load still highlights the built-in findings.
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else Object.assign(root, api);
})(typeof self !== 'undefined' ? self : this, function () {
    const AXE_CORE_SRC = 'https://cdn.jsdelivr.net/npm/axe-core@4.14.0/axe.min.js';
    const AXE_CORE_INTEGRITY = 'sha384-lFUXB8tI4BaTE1bqxH9eeAXON2NH1qsdAhs7S5L++nUNP5QOy+hwr0C3TYEJUEl0';
    const IMPACT_RANK = { critical: 0, serious: 1, moderate: 2, minor: 3 };
    const RULES = {
        'image-alt': ['critical', 'Image is missing an alt attribute. Use alt="" if it is decorative.'],
        'button-name': ['serious', 'Button has no accessible name.'],
        'link-name': ['serious', 'Link has no accessible name.'],
        'label': ['serious', 'Form field has no label.'],
        'document-title': ['serious', 'Document has no title.'],
        'html-has-lang': ['serious', 'html element has no lang attribute.'],
        'frame-title': ['serious', 'Frame has no accessible name.'],
        'empty-heading': ['moderate', 'Heading is empty.'],
        'tabindex': ['serious', 'tabindex is greater than 0.'],
        'duplicate-id': ['moderate', 'Duplicate id.'],
        'aria-hidden-focus': ['serious', 'Focusable element is hidden from assistive technology with aria-hidden.'],
    };

    function collapse(text) {
        return String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
    }

    function normalizeImpact(impact) {
        const value = String(impact || '').toLowerCase();
        return Object.prototype.hasOwnProperty.call(IMPACT_RANK, value) ? value : 'moderate';
    }

    function a11yHelpUrl(url) {
        const value = String(url || '');
        if (!/^https:\/\//i.test(value) || /[\s"'<>]/.test(value)) return '';
        return value;
    }

    function helpFor(id) {
        return a11yHelpUrl(`https://dequeuniversity.com/rules/axe/4.10/${id}`);
    }

    function cssEscape(value) {
        if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(String(value));
        return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
    }

    function isHidden(el) {
        let node = el;
        while (node && node.nodeType === 1) {
            if (node.hasAttribute && node.hasAttribute('hidden')) return true;
            node = node.parentElement;
        }
        return false;
    }

    function isAriaHidden(el) {
        let node = el;
        while (node && node.nodeType === 1) {
            if (node.getAttribute && node.getAttribute('aria-hidden') === 'true') return true;
            node = node.parentElement;
        }
        return false;
    }

    function isDisabled(el) {
        return !!(el && el.hasAttribute && el.hasAttribute('disabled'));
    }

    function labelText(el, doc) {
        let parent = el.parentElement;
        while (parent) {
            if (String(parent.tagName || '').toUpperCase() === 'LABEL') return collapse(parent.textContent);
            parent = parent.parentElement;
        }
        const id = el.getAttribute && el.getAttribute('id');
        if (!id || !doc || typeof doc.querySelector !== 'function') return '';
        let label = null;
        try {
            label = doc.querySelector(`label[for="${cssEscape(id)}"]`);
        } catch (_) {
            label = null;
        }
        return label ? collapse(label.textContent) : '';
    }

    function accessibleName(el, doc) {
        if (!el || !el.getAttribute) return '';
        const aria = collapse(el.getAttribute('aria-label'));
        if (aria) return aria;
        const labelledBy = collapse(el.getAttribute('aria-labelledby'));
        if (labelledBy && doc && typeof doc.getElementById === 'function') {
            const text = labelledBy.split(' ').map(id => {
                const node = doc.getElementById(id);
                return node ? collapse(node.textContent) : '';
            }).filter(Boolean).join(' ');
            const joined = collapse(text);
            if (joined) return joined;
        }
        const tag = String(el.tagName || '').toUpperCase();
        const type = String(el.getAttribute('type') || '').toLowerCase();
        if (tag === 'IMG' || tag === 'AREA' || (tag === 'INPUT' && type === 'image')) {
            return el.hasAttribute('alt') ? collapse(el.getAttribute('alt')) : '';
        }
        if (tag === 'IFRAME') return collapse(el.getAttribute('title'));
        if (tag === 'INPUT') {
            if (type === 'hidden') return '';
            if (type === 'button' || type === 'submit' || type === 'reset') {
                const value = el.getAttribute('value');
                if (value != null && collapse(value)) return collapse(value);
                if (type === 'submit') return 'Submit';
                if (type === 'reset') return 'Reset';
                return '';
            }
            return labelText(el, doc) || collapse(el.getAttribute('title'));
        }
        if (tag === 'SELECT' || tag === 'TEXTAREA') {
            return labelText(el, doc) || collapse(el.getAttribute('title'));
        }
        const role = String(el.getAttribute('role') || '').toLowerCase();
        if (tag === 'BUTTON' || tag === 'A' || /^H[1-6]$/.test(tag) || role === 'button' || role === 'link' || role === 'img') {
            return collapse(el.textContent) || collapse(el.getAttribute('title'));
        }
        return '';
    }

    function isFocusable(el) {
        if (!el || !el.tagName || isHidden(el) || isDisabled(el)) return false;
        const tabindex = el.hasAttribute('tabindex') ? el.getAttribute('tabindex') : null;
        if (tabindex != null && tabindex !== '') {
            const n = Number(tabindex);
            if (!Number.isFinite(n)) return false;
            return n >= 0;
        }
        const tag = String(el.tagName).toUpperCase();
        if (tag === 'A') return el.hasAttribute('href');
        if (tag === 'BUTTON' || tag === 'SELECT' || tag === 'TEXTAREA' || tag === 'SUMMARY') return true;
        if (tag === 'INPUT') return String(el.getAttribute('type') || '').toLowerCase() !== 'hidden';
        return false;
    }

    function elementChildren(parent) {
        if (!parent || !parent.children) return [];
        return Array.from(parent.children).filter(child => child && child.nodeType === 1 && child.tagName);
    }

    function selectorFor(el, idCounts) {
        if (!el || el.nodeType !== 1) return '';
        const id = el.getAttribute ? (el.getAttribute('id') || '') : '';
        const counts = idCounts || new Map();
        if (id && (counts.get(id) || 0) === 1 && /^[A-Za-z][\w:-]*$/.test(id)) return `#${id}`;
        const parts = [];
        let node = el;
        for (let depth = 0; node && node.nodeType === 1 && depth < 8; depth++) {
            const tag = String(node.tagName || '').toLowerCase();
            if (!tag || tag === 'html') break;
            let part = tag;
            const siblings = elementChildren(node.parentElement).filter(child => String(child.tagName).toLowerCase() === tag);
            if (siblings.length > 1) {
                const index = siblings.indexOf(node) + 1;
                if (index > 0) part += `:nth-of-type(${index})`;
            }
            parts.unshift(part);
            if (tag === 'body') break;
            node = node.parentElement;
        }
        return parts.join(' > ');
    }

    function describeElement(el, doc, idCounts) {
        const id = el.getAttribute('id') || '';
        return {
            tag: String(el.tagName || '').toLowerCase(),
            role: String(el.getAttribute('role') || '').toLowerCase(),
            type: String(el.getAttribute('type') || '').toLowerCase(),
            id,
            idCount: id ? (idCounts.get(id) || 0) : 0,
            name: accessibleName(el, doc),
            altPresent: el.hasAttribute('alt'),
            href: el.hasAttribute('href'),
            tabindex: el.hasAttribute('tabindex') ? el.getAttribute('tabindex') : null,
            hidden: isHidden(el),
            ariaHidden: isAriaHidden(el),
            focusable: isFocusable(el),
            selector: selectorFor(el, idCounts),
        };
    }

    function collectA11yInput(doc) {
        if (!doc || typeof doc !== 'object') return { title: '', lang: '', candidates: [] };
        const html = doc.documentElement;
        const lang = html && html.getAttribute
            ? collapse(html.getAttribute('lang') || html.getAttribute('xml:lang'))
            : '';
        const title = collapse(doc.title);
        const root = doc.body || html;
        const all = root && typeof root.querySelectorAll === 'function' ? Array.from(root.querySelectorAll('*')) : [];
        const idCounts = new Map();
        for (const el of all) {
            if (!el || !el.getAttribute) continue;
            const id = el.getAttribute('id');
            if (id) idCounts.set(id, (idCounts.get(id) || 0) + 1);
        }
        const candidates = [];
        for (const el of all) {
            if (!el || !el.tagName) continue;
            const tag = String(el.tagName).toUpperCase();
            if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' || tag === 'TEMPLATE') continue;
            candidates.push(describeElement(el, doc, idCounts));
        }
        return { title, lang, candidates };
    }

    function makeIssue(id, selector, message) {
        const rule = RULES[id] || ['moderate', 'Accessibility issue.'];
        return {
            id,
            impact: rule[0],
            source: 'builtin',
            message: message || rule[1],
            selector: selector || '',
            helpUrl: helpFor(id),
        };
    }

    function nameRule(candidate) {
        if (candidate.tag === 'a' || candidate.role === 'link') {
            return (candidate.href || candidate.role === 'link') ? 'link-name' : null;
        }
        if (candidate.tag === 'button' || candidate.role === 'button') return 'button-name';
        if (candidate.tag === 'input' && (candidate.type === 'button' || candidate.type === 'submit' || candidate.type === 'reset')) {
            return 'button-name';
        }
        if (candidate.tag === 'input' && (candidate.type === 'hidden' || candidate.type === 'image')) return null;
        if (candidate.tag === 'input' || candidate.tag === 'select' || candidate.tag === 'textarea') return 'label';
        return null;
    }

    function judgeA11yCandidates(candidates, docInfo) {
        const info = docInfo || {};
        const issues = [];
        if (!collapse(info.title)) issues.push(makeIssue('document-title', 'html'));
        if (!collapse(info.lang)) issues.push(makeIssue('html-has-lang', 'html'));
        for (const candidate of candidates || []) {
            if (!candidate || !candidate.tag) continue;
            if (candidate.id && candidate.idCount > 1) {
                issues.push(makeIssue('duplicate-id', candidate.selector, `Duplicate id "${candidate.id}".`));
            }
            if (candidate.hidden) continue;
            const image = candidate.tag === 'img' || candidate.tag === 'area' || (candidate.tag === 'input' && candidate.type === 'image');
            if (image && !candidate.altPresent) issues.push(makeIssue('image-alt', candidate.selector));
            else if (candidate.role === 'img' && !candidate.name && !candidate.ariaHidden) {
                issues.push(makeIssue('image-alt', candidate.selector));
            }
            if (candidate.tag === 'iframe' && !candidate.name && !candidate.ariaHidden) {
                issues.push(makeIssue('frame-title', candidate.selector));
            }
            const rule = nameRule(candidate);
            if (rule && !candidate.name && !candidate.ariaHidden) issues.push(makeIssue(rule, candidate.selector));
            if (/^h[1-6]$/.test(candidate.tag) && !candidate.name && !candidate.ariaHidden) {
                issues.push(makeIssue('empty-heading', candidate.selector));
            }
            if (candidate.tabindex != null && candidate.tabindex !== '') {
                const n = Number(candidate.tabindex);
                if (Number.isFinite(n) && n > 0) issues.push(makeIssue('tabindex', candidate.selector));
            }
            if (candidate.focusable && candidate.ariaHidden) issues.push(makeIssue('aria-hidden-focus', candidate.selector));
        }
        return issues;
    }

    function axeSelector(target) {
        const parts = [];
        const list = Array.isArray(target) ? target : [target];
        for (const item of list) {
            if (typeof item === 'string') parts.push(item);
            else if (Array.isArray(item)) {
                for (const sub of item) if (typeof sub === 'string') parts.push(sub);
            }
        }
        return parts.length ? parts[parts.length - 1] : '';
    }

    function issuesFromAxe(results) {
        const violations = results && Array.isArray(results.violations) ? results.violations : [];
        const issues = [];
        for (const violation of violations) {
            const id = String((violation && violation.id) || 'axe');
            const helpUrl = a11yHelpUrl(violation && violation.helpUrl);
            const fallback = collapse(violation && (violation.help || violation.description || violation.id)) || 'Accessibility issue.';
            const nodes = violation && Array.isArray(violation.nodes) && violation.nodes.length ? violation.nodes : [null];
            for (const node of nodes) {
                let message = fallback;
                if (node && node.failureSummary) {
                    const line = collapse(String(node.failureSummary).split('\n')[0]);
                    if (line) message = line;
                }
                if (message.length > 400) message = message.slice(0, 399) + '…';
                issues.push({
                    id,
                    impact: normalizeImpact(node && node.impact || violation.impact),
                    source: 'axe',
                    message,
                    selector: node ? axeSelector(node.target) : '',
                    helpUrl,
                });
            }
        }
        return issues;
    }

    function compareIssues(a, b) {
        const byImpact = (IMPACT_RANK[a.impact] ?? 9) - (IMPACT_RANK[b.impact] ?? 9);
        if (byImpact) return byImpact;
        const bySelector = String(a.selector).localeCompare(String(b.selector));
        if (bySelector) return bySelector;
        return String(a.id).localeCompare(String(b.id));
    }

    function mergeIssues(issues) {
        const map = new Map();
        for (const issue of issues || []) {
            if (!issue || !issue.id) continue;
            const key = `${issue.id}\n${issue.selector || ''}`;
            const prev = map.get(key);
            if (!prev || (prev.source !== 'axe' && issue.source === 'axe')) map.set(key, issue);
        }
        return Array.from(map.values()).sort(compareIssues);
    }

    function stamp(now) {
        const date = now == null ? new Date() : new Date(now);
        if (Number.isNaN(date.getTime())) return new Date(0).toISOString();
        return date.toISOString();
    }

    async function runA11yAudit(doc, options) {
        const opts = options || {};
        const input = collectA11yInput(doc);
        let issues = judgeA11yCandidates(input.candidates, { title: input.title, lang: input.lang });
        let axe = 'skipped';
        let axeError = null;
        if (opts.axe && typeof opts.axe.run === 'function') {
            try {
                const results = await opts.axe.run(opts.context || doc, opts.axeOptions || { resultTypes: ['violations'] });
                issues = issues.concat(issuesFromAxe(results));
                axe = 'used';
            } catch (err) {
                axe = 'error';
                axeError = err && err.message ? String(err.message) : String(err);
            }
        } else if (Object.prototype.hasOwnProperty.call(opts, 'axe')) {
            axe = 'unavailable';
        }
        const merged = mergeIssues(issues);
        return {
            generatedAt: stamp(opts.now),
            axe,
            axeError,
            issueCount: merged.length,
            issues: merged,
        };
    }

    function queryAll(doc, selector) {
        if (!selector || selector === 'html' || selector === 'body') return [];
        try {
            return Array.from(doc.querySelectorAll(selector));
        } catch (_) {
            try {
                const one = doc.querySelector(selector);
                return one ? [one] : [];
            } catch (__) {
                return [];
            }
        }
    }

    function highlightA11yIssues(doc, issues) {
        clearA11yHighlights(doc);
        if (!doc || typeof doc.querySelectorAll !== 'function') return;
        for (const issue of issues || []) {
            const rank = IMPACT_RANK[issue.impact] ?? 9;
            for (const node of queryAll(doc, issue.selector)) {
                if (!node || !node.classList) continue;
                node.classList.add('cc-a11y-hit');
                const prev = node.getAttribute && node.getAttribute('data-a11y-impact');
                if (!prev || rank < (IMPACT_RANK[prev] ?? 9)) {
                    if (node.setAttribute) node.setAttribute('data-a11y-impact', issue.impact || 'minor');
                }
            }
        }
    }

    function clearA11yHighlights(doc) {
        if (!doc || typeof doc.querySelectorAll !== 'function') return;
        doc.querySelectorAll('.cc-a11y-hit').forEach(node => {
            if (node.classList) node.classList.remove('cc-a11y-hit');
            if (node.removeAttribute) node.removeAttribute('data-a11y-impact');
        });
    }

    function a11yStatusText(report) {
        if (!report) return '';
        const count = Number(report.issueCount) || 0;
        let axeNote = 'built-in checks only';
        if (report.axe === 'used') axeNote = 'axe-core included';
        else if (report.axe === 'unavailable') axeNote = 'axe-core unavailable; built-in checks only';
        else if (report.axe === 'error') axeNote = 'axe-core failed; built-in checks only';
        const noun = count === 1 ? 'issue' : 'issues';
        if (count === 0) return `No accessibility issues found (${axeNote}). Snapshot only.`;
        return `${count} accessibility ${noun} highlighted (${axeNote}). Snapshot only.`;
    }

    function a11yReportJson(report) {
        const safe = report && typeof report === 'object' ? report : {};
        const issues = (Array.isArray(safe.issues) ? safe.issues : []).filter(Boolean).map(issue => ({
            id: String(issue.id || ''),
            impact: normalizeImpact(issue.impact),
            source: issue.source === 'axe' ? 'axe' : 'builtin',
            message: String(issue.message || ''),
            selector: String(issue.selector || ''),
            helpUrl: a11yHelpUrl(issue.helpUrl),
        }));
        const body = {
            generatedAt: safe.generatedAt || '',
            axe: safe.axe || 'skipped',
            axeError: safe.axeError || null,
            issueCount: issues.length,
            summary: a11yStatusText({ axe: safe.axe, issueCount: issues.length }),
            issues,
        };
        return JSON.stringify(body, null, 2);
    }

    function defaultInjectScript(doc, spec) {
        return new Promise((resolve, reject) => {
            if (!doc || typeof doc.createElement !== 'function') {
                reject(new Error('axe-core failed to load'));
                return;
            }
            const existing = typeof doc.querySelector === 'function'
                ? doc.querySelector('script[data-cc-axe="1"]')
                : null;
            if (existing) {
                const state = existing.getAttribute && existing.getAttribute('data-cc-axe-state');
                if (state === 'ready') { resolve(); return; }
                if (state === 'error') { reject(new Error('axe-core failed to load')); return; }
                existing.addEventListener('load', () => resolve());
                existing.addEventListener('error', () => reject(new Error('axe-core failed to load')));
                return;
            }
            const script = doc.createElement('script');
            script.src = spec.src;
            script.async = true;
            script.crossOrigin = 'anonymous';
            if (spec.integrity) script.integrity = spec.integrity;
            if (script.setAttribute) {
                script.setAttribute('data-cc-axe', '1');
                script.setAttribute('data-cc-axe-state', 'loading');
            }
            script.onload = () => {
                if (script.setAttribute) script.setAttribute('data-cc-axe-state', 'ready');
                resolve();
            };
            script.onerror = () => {
                if (script.remove) script.remove();
                reject(new Error('axe-core failed to load'));
            };
            const parent = doc.head || doc.documentElement || doc.body;
            if (!parent || typeof parent.appendChild !== 'function') {
                reject(new Error('axe-core failed to load'));
                return;
            }
            parent.appendChild(script);
        });
    }

    function loadOptionalAxe(doc, options) {
        const opts = options || {};
        if (opts.axe && typeof opts.axe.run === 'function') return Promise.resolve(opts.axe);
        const win = opts.window || (typeof window !== 'undefined' ? window : null);
        if (win && win.axe && typeof win.axe.run === 'function') return Promise.resolve(win.axe);
        if (opts.load === false) return Promise.resolve(null);
        const inject = opts.injectScript || defaultInjectScript;
        return Promise.resolve()
            .then(() => inject(doc, {
                src: opts.src || AXE_CORE_SRC,
                integrity: opts.integrity || AXE_CORE_INTEGRITY,
            }))
            .then(() => {
                const loaded = opts.window || (typeof window !== 'undefined' ? window : null);
                return loaded && loaded.axe && typeof loaded.axe.run === 'function' ? loaded.axe : null;
            })
            .catch(() => null);
    }

    return {
        AXE_CORE_SRC,
        AXE_CORE_INTEGRITY,
        collectA11yInput,
        judgeA11yCandidates,
        issuesFromAxe,
        mergeIssues,
        runA11yAudit,
        highlightA11yIssues,
        clearA11yHighlights,
        a11yStatusText,
        a11yReportJson,
        a11yHelpUrl,
        loadOptionalAxe,
    };
});
