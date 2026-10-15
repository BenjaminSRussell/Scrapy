import test from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_EVENTS, initialState, applySnapshot, applyTick, freshEvents, filterEvents, healthSummary, edgeFor,
} from '../app/state.js';

const stage = (id, extra = {}) => ({ id, title: id, status: 'ok', count: 1, rate_per_min: 1, freshness: 'active', ...extra });
const ev = (n, kind = 'discovered', extra = {}) => ({ id: `discovery:${n}`, stage: 'discovery', kind, url: `https://a.edu/p${n}`, t: n, fields: {}, ...extra });

function snap(extra = {}) {
  return {
    mode: 'demo', generated_at: 100, interval: 1, poll_error: null,
    stages: [stage('seeds'), stage('discovery')], edges: [{ from: 'seeds', to: 'discovery' }],
    services: [{ id: 'redis', status: 'ok' }],
    series: { discovery: [[0, 1], [10, 2]] },
    recent: [ev(3), ev(2), ev(1)],
    ...extra,
  };
}

test('applySnapshot replaces everything and keeps events newest first', () => {
  const s = applySnapshot(initialState(), snap(), 1000);
  assert.equal(s.mode, 'demo');
  assert.deepEqual(s.events.map((e) => e.id), ['discovery:3', 'discovery:2', 'discovery:1']);
  assert.equal(s.lastUpdate, 1000);
  assert.deepEqual(s.series.discovery, [[0, 1], [10, 2]]);
  assert.equal(initialState().events.length, 0, 'initial state untouched');
});

test('applyTick prepends new events, dedupes by id and caps the list', () => {
  let s = applySnapshot(initialState(), snap(), 1000);
  s = applyTick(s, { generated_at: 101, stages: [stage('discovery', { count: 9 })], services: [], series_point: {}, events: [ev(3), ev(4), ev(5)] }, 1001);
  assert.deepEqual(s.events.slice(0, 3).map((e) => e.id), ['discovery:5', 'discovery:4', 'discovery:3']);
  assert.equal(s.events.filter((e) => e.id === 'discovery:3').length, 1);
  assert.equal(s.stages.find((x) => x.id === 'discovery').count, 9);
  assert.equal(s.stages.find((x) => x.id === 'seeds').count, 1, 'stages missing from a tick keep their last value');
  const many = Array.from({ length: 700 }, (_, i) => ev(1000 + i));
  s = applyTick(s, { generated_at: 102, stages: [], services: [], series_point: {}, events: many }, 1002);
  assert.equal(s.events.length, MAX_EVENTS);
  assert.equal(s.events[0].id, 'discovery:1699');
});

test('applyTick replaces the open series bucket and appends a new one', () => {
  let s = applySnapshot(initialState(), snap(), 1000);
  s = applyTick(s, { generated_at: 101, stages: [], services: [], series_point: { discovery: [10, 7] }, events: [] }, 1001);
  assert.deepEqual(s.series.discovery, [[0, 1], [10, 7]]);
  s = applyTick(s, { generated_at: 111, stages: [], services: [], series_point: { discovery: [20, 3], analysis: [20, 1] }, events: [] }, 1011);
  assert.deepEqual(s.series.discovery, [[0, 1], [10, 7], [20, 3]]);
  assert.deepEqual(s.series.analysis, [[20, 1]]);
});

test('freshEvents returns only unseen, non-replay events for animation', () => {
  const s = applySnapshot(initialState(), snap(), 1000);
  const tick = { events: [ev(3), ev(4), ev(5, 'discovered', { replay: true })] };
  assert.deepEqual(freshEvents(s, tick).map((e) => e.id), ['discovery:4']);
});

test('filterEvents by kind group and case-insensitive URL substring', () => {
  const events = [ev(1), ev(2, 'analyzed'), ev(3, 'summarized'), ev(4, 'large_doc'), ev(5, 'error'), ev(6, 'seeded')];
  assert.equal(filterEvents(events, 'all', '').length, 6);
  assert.deepEqual(filterEvents(events, 'found', '').map((e) => e.kind), ['discovered', 'seeded']);
  assert.deepEqual(filterEvents(events, 'summarized', '').map((e) => e.kind), ['summarized', 'large_doc']);
  assert.deepEqual(filterEvents(events, 'errors', '').map((e) => e.kind), ['error']);
  assert.deepEqual(filterEvents(events, 'all', 'P3').map((e) => e.id), ['discovery:3']);
  assert.deepEqual(filterEvents(events, 'all', '  ').length, 6);
});

test('healthSummary counts unhealthy services and unreadable stages', () => {
  assert.deepEqual(healthSummary([{ status: 'ok' }], [stage('a')]), { issues: 0, label: 'All systems normal' });
  assert.deepEqual(
    healthSummary([{ status: 'down' }, { status: 'warn' }, { status: 'ok' }, { status: 'unknown' }], [stage('a', { status: 'error' }), stage('b', { status: 'missing' })]),
    { issues: 3, label: '3 issues' },
  );
  assert.equal(healthSummary([{ status: 'down' }], []).label, '1 issue');
});

test('edgeFor maps an event to the pipeline edge it travels', () => {
  assert.deepEqual(edgeFor(ev(1, 'discovered')), ['seeds', 'discovery']);
  assert.deepEqual(edgeFor(ev(1, 'analyzed')), ['discovery', 'analysis']);
  assert.deepEqual(edgeFor(ev(1, 'summarized')), ['analysis', 'summaries']);
  assert.deepEqual(edgeFor(ev(1, 'large_doc')), ['analysis', 'large_docs']);
  assert.equal(edgeFor(ev(1, 'error')), null);
  assert.equal(edgeFor(ev(1, 'seeded')), null);
});
