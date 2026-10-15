// Staging-only chaos game-day panel (#967). The server decides whether the
// buttons exist; this file only paints that decision and never calls Redis
// or Kafka itself.
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.ChaosGameday = api;
})(typeof self !== 'undefined' ? self : this, function () {
    const ACTIONS = [
        { id: 'kill-redis', label: 'Kill Redis (stub)' },
        { id: 'pause-kafka', label: 'Pause Kafka (stub)' },
    ];
    const OFF_MESSAGE = 'Game-day fault injection is off. It runs only when the environment is staging and CC_CHAOS_GAMEDAY is on. These buttons never run in production.';
    const ON_MESSAGE = 'Staging game day is armed. Kill Redis and Pause Kafka record a stub only. They do not stop Redis or Kafka.';
    const REFUSED = 'Fault injection refused. Nothing was changed.';

    function panelState(status) {
        const on = !!(status && status.ok === true && status.enabled === true
            && status.stub === true && status.effect === 'none' && status.env === 'staging');
        return {
            enabled: on,
            message: on ? ON_MESSAGE : OFF_MESSAGE,
            actions: on ? ACTIONS.map(function (action) { return action.id; }) : [],
        };
    }

    function applyChaosPanel(doc, status) {
        if (!doc || typeof doc.getElementById !== 'function') return;
        const state = panelState(status);
        const note = doc.getElementById('chaos-gameday-status');
        if (note) note.textContent = state.message;
        for (let i = 0; i < ACTIONS.length; i++) {
            const btn = doc.getElementById('chaos-btn-' + ACTIONS[i].id);
            if (!btn) continue;
            const show = state.enabled;
            btn.hidden = !show;
            btn.disabled = !show;
        }
        if (!state.enabled) {
            const result = doc.getElementById('chaos-gameday-result');
            if (result) result.textContent = '';
        }
    }

    function describeInjection(body) {
        const accepted = body && body.accepted;
        const known = ACTIONS.some(function (action) { return action.id === accepted; });
        if (!body || body.ok !== true || body.stub !== true || body.effect !== 'none' || !known) {
            return { level: 'danger', text: REFUSED };
        }
        const label = ACTIONS.find(function (action) { return action.id === accepted; }).label;
        return {
            level: 'warning',
            text: 'Recorded ' + label + '. No Redis or Kafka process was touched.',
        };
    }

    return {
        ACTIONS: ACTIONS,
        OFF_MESSAGE: OFF_MESSAGE,
        ON_MESSAGE: ON_MESSAGE,
        REFUSED: REFUSED,
        panelState: panelState,
        applyChaosPanel: applyChaosPanel,
        describeInjection: describeInjection,
    };
});
