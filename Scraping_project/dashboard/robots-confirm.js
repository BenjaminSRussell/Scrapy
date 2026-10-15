// Stage 1 UI gate for rustmapper ignore_robots (#938).
// Checkboxes default off. The server (POST /api/stage1/ignore-robots) decides
// and writes the audit log. This file only decides when the form may submit.
(function (root, factory) {
    var api = factory();
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    root.stage1RobotsConfirm = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    var CONFIRM_PHRASE = 'IGNORE ROBOTS';

    function dualConfirmReady(payload) {
        if (!payload || payload.rustmapper_ignore_robots !== true || payload.stage1_confirm !== true) {
            return false;
        }
        if (!String(payload.reason || '').trim()) {
            return false;
        }
        return String(payload.typed || '').trim() === CONFIRM_PHRASE;
    }

    function readForm(doc) {
        return {
            rustmapper_ignore_robots: !!doc.getElementById('rustmapper-ignore').checked,
            stage1_confirm: !!doc.getElementById('stage1-confirm').checked,
            reason: doc.getElementById('robots-reason').value,
            typed: doc.getElementById('robots-typed').value,
            actor: 'operator'
        };
    }

    function bind(doc) {
        var button = doc.getElementById('robots-submit');
        var result = doc.getElementById('robots-result');

        function refresh() {
            button.disabled = !dualConfirmReady(readForm(doc));
        }

        ['rustmapper-ignore', 'stage1-confirm', 'robots-reason', 'robots-typed'].forEach(function (id) {
            doc.getElementById(id).addEventListener('input', refresh);
            doc.getElementById(id).addEventListener('change', refresh);
        });
        refresh();

        button.addEventListener('click', function () {
            var payload = readForm(doc);
            if (!dualConfirmReady(payload)) {
                result.textContent = 'refused: both sides, a reason, and the phrase are required';
                return;
            }
            result.textContent = 'recording…';
            fetch('/api/stage1/ignore-robots', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).then(function (response) {
                return response.json().then(function (body) {
                    return { ok: response.ok, body: body };
                });
            }).then(function (out) {
                var body = out.body || {};
                var obey = body.robotstxt_obey === false ? 'false' : 'true';
                result.textContent = 'outcome=' + (body.outcome || 'error') + ' ROBOTSTXT_OBEY=' + obey
                    + (body.detail ? '\n' + body.detail : '');
            }).catch(function () {
                result.textContent = 'could not record the decision';
            });
        });
    }

    if (typeof document !== 'undefined' && document.getElementById('robots-submit')) {
        bind(document);
    }

    return { CONFIRM_PHRASE: CONFIRM_PHRASE, dualConfirmReady: dualConfirmReady, readForm: readForm, bind: bind };
}));
