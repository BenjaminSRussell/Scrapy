// Accessible confirm dialog for destructive job actions (#995).
// Focus stays inside while open. Esc and Cancel dismiss without running the
// action. Confirm runs its callback once, then returns focus to the opener.
// Text is assigned with textContent only so job copy cannot inject markup.
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else Object.assign(root, api);
})(typeof self !== 'undefined' ? self : this, function () {
    // stop-all-stages is the action the job controls will confirm (#995, #1000).
    const JOB_ACTIONS = {
        'stop-all-stages': {
            title: 'Stop all stages?',
            message: 'Stop every running pipeline stage. In-flight work is dropped. Confirm only if you mean to stop the pipeline.',
            confirmLabel: 'Stop all stages',
            cancelLabel: 'Cancel',
        },
    };

    const controllers = new WeakMap();

    function setHidden(el, hidden) {
        if (!el) return;
        el.hidden = !!hidden;
        if (hidden) el.setAttribute('hidden', '');
        else el.removeAttribute('hidden');
    }

    function isHidden(el) {
        let node = el;
        while (node) {
            if (node.hidden) return true;
            if (typeof node.hasAttribute === 'function' && node.hasAttribute('hidden')) return true;
            node = node.parentElement || null;
        }
        return false;
    }

    function isFocusable(el) {
        if (!el || el.nodeType === 3 || el.disabled) return false;
        if (isHidden(el)) return false;
        const tab = typeof el.getAttribute === 'function' ? el.getAttribute('tabindex') : null;
        if (tab === '-1') return false;
        const tag = String(el.tagName || '').toUpperCase();
        if (tag === 'A') return !!(el.getAttribute && el.getAttribute('href'));
        if (tag === 'INPUT') {
            const type = String((el.getAttribute && el.getAttribute('type')) || '').toLowerCase();
            if (type === 'hidden') return false;
        }
        if (tab !== null) return true;
        return tag === 'BUTTON' || tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
    }

    function listFocusable(root) {
        const out = [];
        function walk(node) {
            const kids = node.children;
            if (!kids) return;
            for (let i = 0; i < kids.length; i++) {
                const child = kids[i];
                if (isFocusable(child)) out.push(child);
                walk(child);
            }
        }
        walk(root);
        return out;
    }

    function contains(root, node) {
        if (!root || !node) return false;
        if (root === node) return true;
        if (typeof root.contains === 'function') return root.contains(node);
        return false;
    }

    function ensureDialog(doc) {
        let dialog = doc.getElementById('cc-confirm-dialog');
        if (dialog) {
            if (dialog.getAttribute('tabindex') === null) dialog.setAttribute('tabindex', '-1');
            return dialog;
        }
        dialog = doc.createElement('div');
        dialog.setAttribute('id', 'cc-confirm-dialog');
        dialog.setAttribute('class', 'cc-modal');
        dialog.setAttribute('role', 'alertdialog');
        dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('aria-labelledby', 'cc-confirm-title');
        dialog.setAttribute('aria-describedby', 'cc-confirm-message');
        dialog.setAttribute('tabindex', '-1');
        setHidden(dialog, true);

        const backdrop = doc.createElement('div');
        backdrop.setAttribute('class', 'cc-modal-backdrop');

        const panel = doc.createElement('div');
        panel.setAttribute('class', 'cc-modal-panel');

        const title = doc.createElement('h2');
        title.setAttribute('id', 'cc-confirm-title');
        title.setAttribute('class', 'cc-modal-title');
        title.textContent = 'Confirm destructive action';

        const message = doc.createElement('p');
        message.setAttribute('id', 'cc-confirm-message');
        message.setAttribute('class', 'cc-modal-message');
        message.textContent = 'This action cannot be undone.';

        const actions = doc.createElement('div');
        actions.setAttribute('class', 'cc-modal-actions');

        const cancelBtn = doc.createElement('button');
        cancelBtn.setAttribute('type', 'button');
        cancelBtn.setAttribute('id', 'cc-confirm-cancel');
        cancelBtn.setAttribute('class', 'btn cc-modal-btn');
        cancelBtn.textContent = 'Cancel';

        const okBtn = doc.createElement('button');
        okBtn.setAttribute('type', 'button');
        okBtn.setAttribute('id', 'cc-confirm-ok');
        okBtn.setAttribute('class', 'btn cc-modal-btn cc-modal-btn-danger');
        okBtn.textContent = 'Confirm';

        actions.appendChild(cancelBtn);
        actions.appendChild(okBtn);
        panel.appendChild(title);
        panel.appendChild(message);
        panel.appendChild(actions);
        dialog.appendChild(backdrop);
        dialog.appendChild(panel);
        doc.body.appendChild(dialog);
        return dialog;
    }

    function textOr(value, fallback) {
        if (value == null || value === '') return fallback;
        return String(value);
    }

    function createConfirmDialog(doc) {
        if (!doc || !doc.body || typeof doc.getElementById !== 'function' || typeof doc.createElement !== 'function') {
            return null;
        }
        if (controllers.has(doc)) return controllers.get(doc);

        const dialog = ensureDialog(doc);
        const state = {
            open: false,
            fired: false,
            onConfirm: null,
            returnFocus: null,
            lastInside: null,
            guardFocus: false,
            hosts: null,
            prevOverflow: '',
        };

        function setText(id, value) {
            const el = doc.getElementById(id);
            if (el) el.textContent = value;
        }

        function rememberFocus(trigger) {
            const candidate = trigger || doc.activeElement;
            if (!candidate || candidate === dialog || contains(dialog, candidate)) return null;
            return candidate;
        }

        function hideBackground() {
            const hosts = [];
            const kids = doc.body.children;
            for (let i = 0; i < kids.length; i++) {
                const el = kids[i];
                if (el === dialog) continue;
                const tag = String(el.tagName || '').toUpperCase();
                if (tag === 'SCRIPT' || tag === 'STYLE') continue;
                const snap = {
                    el: el,
                    hadAriaHidden: typeof el.hasAttribute === 'function' && el.hasAttribute('aria-hidden'),
                    ariaHidden: typeof el.getAttribute === 'function' ? el.getAttribute('aria-hidden') : null,
                    inertSupported: 'inert' in el,
                    inert: 'inert' in el ? el.inert : undefined,
                };
                if (typeof el.setAttribute === 'function') el.setAttribute('aria-hidden', 'true');
                if (snap.inertSupported) el.inert = true;
                hosts.push(snap);
            }
            state.hosts = hosts;
            if (doc.body.style) {
                state.prevOverflow = doc.body.style.overflow || '';
                doc.body.style.overflow = 'hidden';
            }
        }

        function restoreBackground() {
            const hosts = state.hosts || [];
            for (let i = 0; i < hosts.length; i++) {
                const snap = hosts[i];
                if (snap.hadAriaHidden) snap.el.setAttribute('aria-hidden', snap.ariaHidden == null ? '' : snap.ariaHidden);
                else if (typeof snap.el.removeAttribute === 'function') snap.el.removeAttribute('aria-hidden');
                if (snap.inertSupported) snap.el.inert = snap.inert;
            }
            state.hosts = null;
            if (doc.body.style) doc.body.style.overflow = state.prevOverflow || '';
        }

        function focusInitial() {
            const cancel = doc.getElementById('cc-confirm-cancel');
            const items = listFocusable(dialog);
            const target = (cancel && isFocusable(cancel)) ? cancel : (items[0] || dialog);
            state.lastInside = items.indexOf(target) === -1 ? (items[0] || null) : target;
            if (target && typeof target.focus === 'function') target.focus();
        }

        function pullFocusBack(target) {
            const items = listFocusable(dialog);
            let fallback = state.lastInside;
            if (!fallback || items.indexOf(fallback) === -1) fallback = items[0] || dialog;
            if (!fallback || fallback === target) return;
            state.guardFocus = true;
            try {
                fallback.focus();
            } finally {
                state.guardFocus = false;
            }
        }

        function onFocusIn(event) {
            if (!state.open || state.guardFocus) return;
            const target = event && event.target;
            if (target && contains(dialog, target)) {
                if (isFocusable(target)) state.lastInside = target;
                return;
            }
            pullFocusBack(target);
        }

        function onKeyDown(event) {
            if (!state.open || !event) return;
            const key = event.key;
            if ((key === 'Escape' || key === 'Esc') && !event.ctrlKey && !event.metaKey && !event.altKey) {
                if (typeof event.preventDefault === 'function') event.preventDefault();
                if (typeof event.stopPropagation === 'function') event.stopPropagation();
                dismiss();
                return;
            }
            if (key !== 'Tab' || event.ctrlKey || event.metaKey || event.altKey) return;
            const items = listFocusable(dialog);
            if (typeof event.preventDefault === 'function') event.preventDefault();
            if (typeof event.stopPropagation === 'function') event.stopPropagation();
            if (!items.length) {
                if (typeof dialog.focus === 'function') dialog.focus();
                return;
            }
            const active = doc.activeElement;
            let index = -1;
            for (let i = 0; i < items.length; i++) {
                if (items[i] === active) { index = i; break; }
            }
            const delta = event.shiftKey ? -1 : 1;
            const next = index === -1
                ? (event.shiftKey ? items[items.length - 1] : items[0])
                : items[(index + delta + items.length) % items.length];
            state.lastInside = next;
            next.focus();
        }

        function open(opts) {
            if (state.open) return false;
            opts = opts || {};
            state.fired = false;
            state.onConfirm = typeof opts.onConfirm === 'function' ? opts.onConfirm : null;
            state.returnFocus = rememberFocus(opts.trigger || null);
            setText('cc-confirm-title', textOr(opts.title, 'Confirm destructive action'));
            setText('cc-confirm-message', textOr(opts.message, 'This action cannot be undone.'));
            setText('cc-confirm-cancel', textOr(opts.cancelLabel, 'Cancel'));
            setText('cc-confirm-ok', textOr(opts.confirmLabel, 'Confirm'));
            state.open = true;
            setHidden(dialog, false);
            hideBackground();
            focusInitial();
            return true;
        }

        function close() {
            if (!state.open) return false;
            state.open = false;
            state.onConfirm = null;
            setHidden(dialog, true);
            restoreBackground();
            const back = state.returnFocus;
            state.returnFocus = null;
            state.lastInside = null;
            if (back && typeof back.focus === 'function' && !back.disabled) {
                try { back.focus(); } catch (_) { /* opener left the document */ }
            }
            return true;
        }

        function dismiss() {
            if (!state.open || state.fired) return false;
            return close();
        }

        function confirm() {
            if (!state.open || state.fired) return false;
            state.fired = true;
            const fn = state.onConfirm;
            close();
            if (typeof fn === 'function') fn();
            return true;
        }

        function openAction(action, onConfirm, trigger) {
            if (typeof action !== 'string') return false;
            const preset = JOB_ACTIONS[action];
            if (!preset) return false;
            return open({
                title: preset.title,
                message: preset.message,
                confirmLabel: preset.confirmLabel,
                cancelLabel: preset.cancelLabel,
                onConfirm: onConfirm,
                trigger: trigger || null,
            });
        }

        const cancelBtn = doc.getElementById('cc-confirm-cancel');
        const okBtn = doc.getElementById('cc-confirm-ok');
        if (cancelBtn) cancelBtn.addEventListener('click', function (event) {
            if (event && typeof event.preventDefault === 'function') event.preventDefault();
            dismiss();
        });
        if (okBtn) okBtn.addEventListener('click', function (event) {
            if (event && typeof event.preventDefault === 'function') event.preventDefault();
            confirm();
        });
        doc.addEventListener('keydown', onKeyDown, true);
        doc.addEventListener('focusin', onFocusIn, true);

        const api = {
            open: open,
            openAction: openAction,
            close: dismiss,
            isOpen: function () { return state.open; },
            element: dialog,
        };
        controllers.set(doc, api);
        return api;
    }

    return { createConfirmDialog: createConfirmDialog, JOB_ACTIONS: JOB_ACTIONS };
});
