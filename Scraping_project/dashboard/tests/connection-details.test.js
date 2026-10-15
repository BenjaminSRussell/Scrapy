'use strict';
// #979: connection-details popover shows the live metrics endpoint and interval.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('path');
const { connectionDetails, resolveMetricsUrl } = require('../format-utils.js');

test('connectionDetails shows the endpoint and a 5s interval (#979)', () => {
    const view = connectionDetails('/api/metrics', 5000);
    assert.strictEqual(view.endpoint, '/api/metrics');
    assert.strictEqual(view.intervalMs, 5000);
    assert.strictEqual(view.intervalLabel, '5s');
});

test('connectionDetails follows a changed endpoint and interval (#979)', () => {
    const first = connectionDetails(resolveMetricsUrl({ protocol: 'http:', search: '' }, null), 5000);
    const next = connectionDetails(
        resolveMetricsUrl({ protocol: 'http:', search: '?metrics=https://other.example/metrics' }, null),
        30000
    );
    assert.strictEqual(first.endpoint, '/api/metrics');
    assert.strictEqual(first.intervalLabel, '5s');
    assert.strictEqual(next.endpoint, 'https://other.example/metrics');
    assert.strictEqual(next.intervalLabel, '30s');
    const override = connectionDetails(
        resolveMetricsUrl({ protocol: 'http:', search: '?metrics=https://other.example/metrics' }, 'https://override.example/metrics'),
        10000
    );
    assert.strictEqual(override.endpoint, 'https://override.example/metrics');
    assert.strictEqual(override.intervalLabel, '10s');
    assert.notStrictEqual(first.endpoint, next.endpoint);
    assert.notStrictEqual(next.endpoint, override.endpoint);
});

test('connectionDetails rejects an interval outside the picker (#979)', () => {
    const view = connectionDetails('http://localhost:9090/metrics', 1);
    assert.strictEqual(view.endpoint, 'http://localhost:9090/metrics');
    assert.strictEqual(view.intervalMs, 5000);
    assert.strictEqual(view.intervalLabel, '5s');
    assert.strictEqual(connectionDetails(null, null).endpoint, '');
});

test('refresh bar has an info button and popover for endpoint + 5s (#979)', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    const bar = html.slice(html.indexOf('class="refresh-bar"'));
    assert.match(bar, /id="connection-details-btn"[^>]*aria-expanded="false"/);
    assert.match(bar, /aria-controls="connection-details-popover"/);
    assert.match(bar, /aria-label="Connection details"/);
    assert.match(bar, /<svg class="ui-icon"[^>]*aria-hidden="true"/);
    assert.match(bar, /id="connection-details-popover"[^>]*role="dialog"/);
    assert.match(bar, /id="connection-details-endpoint"/);
    assert.match(bar, /id="connection-details-interval">5s</);
    assert.match(app, /function metricsUrl\(\)/);
    assert.match(app, /fetchText\(metricsUrl\(\)\)/);
    assert.match(app, /connectionDetails\(metricsUrl\(\), interval\)/);
    assert.match(app, /setupConnectionDetails\(\)/);
    assert.match(app, /renderConnectionDetails\(\)/);
});
