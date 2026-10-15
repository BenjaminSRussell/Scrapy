import test from 'node:test';
import assert from 'node:assert/strict';
import { squarify } from '../app/layout/squarify.js';
import { createBudget } from '../app/layout/budget.js';
import { linear, niceTicks, monotonePath, areaPath } from '../app/layout/scale.js';

const close = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test('squarify fills the box exactly with every rect inside it', () => {
  const items = [6, 6, 4, 3, 2, 2, 1].map((value, i) => ({ id: i, value }));
  const rects = squarify(items, 0, 0, 600, 400);
  assert.equal(rects.length, 7);
  close(rects.reduce((a, r) => a + r.w * r.h, 0), 600 * 400);
  for (const r of rects) {
    assert.ok(r.x >= -1e-9 && r.y >= -1e-9 && r.x + r.w <= 600 + 1e-6 && r.y + r.h <= 400 + 1e-6, JSON.stringify(r));
  }
  close(rects.find((r) => r.id === 0).w * rects.find((r) => r.id === 0).h, 600 * 400 * (6 / 24));
});

test('squarify keeps tiles reasonably square and ignores empty values', () => {
  const rects = squarify([{ value: 1 }, { value: 1 }, { value: 1 }, { value: 1 }, { value: 0 }, { value: -2 }], 0, 0, 100, 100);
  assert.equal(rects.length, 4);
  for (const r of rects) assert.ok(Math.max(r.w / r.h, r.h / r.w) <= 2.5);
  assert.deepEqual(squarify([], 0, 0, 10, 10), []);
  assert.deepEqual(squarify([{ value: 3 }], 0, 0, 0, 10), []);
});

test('the particle budget caps each edge per second and the total in flight', () => {
  let now = 0;
  const b = createBudget({ perEdgePerSec: 6, total: 10, clock: () => now });
  let granted = 0;
  for (let i = 0; i < 20; i++) granted += b.take('a>b') ? 1 : 0;
  assert.equal(granted, 6);
  now = 1001;
  for (let i = 0; i < 20; i++) granted += b.take('b>c') ? 1 : 0;
  assert.equal(granted, 10, 'total in flight caps at 10');
  b.release(); b.release();
  assert.equal(b.inFlight, 8);
  assert.equal(b.take('b>c'), true, 'b>c used 4 of its 6 this second and 8 of 10 are in flight');
  assert.equal(b.take('b>c'), true);
  assert.equal(b.take('b>c'), false, 'total cap again');
  b.release();
  now = 2500;
  assert.equal(b.take('a>b'), true, 'a>b slots from t=0 have expired');
  b.release(); b.release(); b.release(); b.release(); b.release(); b.release(); b.release(); b.release(); b.release(); b.release(); b.release();
  assert.equal(b.inFlight, 0, 'release never goes negative');
});

test('linear maps a domain to a range and survives a flat domain', () => {
  const s = linear([0, 10], [100, 0]);
  assert.equal(s(0), 100);
  assert.equal(s(10), 0);
  assert.equal(s(5), 50);
  assert.equal(linear([3, 3], [0, 10])(3), 5);
});

test('niceTicks start at zero and cover the max with round steps', () => {
  assert.deepEqual(niceTicks(87), [0, 50, 100]);
  assert.deepEqual(niceTicks(120), [0, 100, 200]);
  assert.deepEqual(niceTicks(9), [0, 5, 10]);
  assert.deepEqual(niceTicks(0), [0, 1]);
  assert.deepEqual(niceTicks(null), [0, 1]);
  assert.deepEqual(niceTicks(0.3), [0, 0.2, 0.4]);
});

test('monotonePath handles 0, 1 and many points without overshooting', () => {
  assert.equal(monotonePath([]), '');
  assert.equal(monotonePath([[1, 2]]), 'M1,2');
  const d = monotonePath([[0, 10], [10, 0], [20, 0], [30, 10]]);
  assert.ok(d.startsWith('M0,10C'));
  const ys = [...d.matchAll(/-?\d+(?:\.\d+)?/g)].map(Number).filter((_, i) => i % 2 === 1);
  assert.ok(Math.min(...ys) >= 0 - 1e-9 && Math.max(...ys) <= 10 + 1e-9, 'control points stay within the data range');
  assert.ok(areaPath([[0, 5], [10, 2]], 20).endsWith('L10,20L0,20Z'));
  assert.equal(areaPath([], 20), '');
});
