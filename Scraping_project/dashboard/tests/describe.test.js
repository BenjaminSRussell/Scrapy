import test from 'node:test';
import assert from 'node:assert/strict';
import { stageCard, stageLabel } from '../app/describe.js';
import { STAGE_BY_ID } from '../app/meta.js';

const now = 10_000;
const s = (extra) => ({ id: 'discovery', status: 'ok', count: 5700, rate_per_min: 84.2, last_write: now - 3, freshness: 'active', errors: null, ...extra });

test('an active stage shows its rate', () => {
  assert.deepEqual(stageCard(s(), now), { count: '5.7K', sub: '84/min', tone: 'good' });
});

test('an active stage with no writes in the last minute shows when it last wrote, not 0/min', () => {
  assert.deepEqual(stageCard(s({ rate_per_min: 0, last_write: now - 70 }), now), { count: '5.7K', sub: 'Active · 1m ago', tone: 'muted' });
  assert.deepEqual(stageCard(s({ rate_per_min: null, last_write: now - 70 }), now).sub, 'Active · 1m ago');
});

test('idle, missing and unreadable stages say so instead of showing numbers', () => {
  assert.deepEqual(stageCard(s({ freshness: 'idle', last_write: now - 600 }), now), { count: '5.7K', sub: 'Idle · 10m ago', tone: 'muted' });
  assert.deepEqual(stageCard(s({ status: 'missing', count: null }), now), { count: '—', sub: 'No data yet', tone: 'muted' });
  assert.deepEqual(stageCard(s({ status: 'error', count: null }), now), { count: '—', sub: 'Can’t read table', tone: 'bad' });
  assert.deepEqual(stageCard(undefined, now).sub, 'No data yet');
});

test('the accessible label reads the full number, the rate and errors', () => {
  const meta = STAGE_BY_ID.discovery;
  assert.equal(stageLabel(meta, s({ errors: 3 }), now), 'Discovery: 5,700 URLs, 84 per minute, 3 errors. Open details.');
  assert.equal(stageLabel(meta, s({ freshness: 'stale', last_write: now - 7200 }), now), 'Discovery: 5,700 URLs, Stale, last write 2h ago. Open details.');
  assert.equal(stageLabel(meta, s({ status: 'error' }), now), 'Discovery: the table can’t be read. Open details.');
});
