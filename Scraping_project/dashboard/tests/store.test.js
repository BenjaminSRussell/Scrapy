import test from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from '../app/store.js';

class FakeES {
  static all = [];
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.listeners = {};
    FakeES.all.push(this);
  }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  emit(type, data) {
    (this.listeners[type] || []).forEach((fn) => fn({ data: JSON.stringify(data) }));
  }
  close() {
    this.readyState = 2;
    this.closed = true;
  }
}

const snap = (generated_at) => ({
  mode: 'live', generated_at, interval: 2, poll_error: null, stages: [], edges: [], services: [], series: {}, recent: [],
});
const tick = (generated_at) => ({ generated_at, stages: [], services: [], series_point: {}, events: [] });

function setup() {
  FakeES.all = [];
  let now = 1000;
  let watchdog = null;
  const store = createStore({
    EventSourceImpl: FakeES,
    clock: () => now,
    setIntervalImpl: (fn) => { watchdog = fn; return 1; },
    setTimeoutImpl: (fn) => { fn(); return 1; },
    clearTimeoutImpl: () => {},
  });
  return { store, advance: (s) => { now += s; }, check: () => watchdog() };
}

test('a stream that keeps advancing stays live', () => {
  const { store, advance, check } = setup();
  FakeES.all[0].emit('snapshot', snap(100));
  for (let i = 1; i <= 20; i++) {
    advance(2);
    FakeES.all[0].emit('tick', tick(100 + 2 * i));
    check();
  }
  assert.equal(store.get().conn, 'live');
  assert.equal(FakeES.all.length, 1);
});

test('a silent stream stops claiming Live and reconnects', () => {
  const { store, advance, check } = setup();
  FakeES.all[0].emit('snapshot', snap(100));
  advance(10);
  check();
  assert.equal(store.get().conn, 'live', 'under max(3 x interval, 20 s) is fine');
  advance(15);
  check();
  assert.equal(store.get().conn, 'reconnecting');
  assert.ok(FakeES.all[0].closed);
  assert.equal(FakeES.all.length, 2, 'a fresh EventSource is opened');
});

test('a reconnect that only replays the same frozen snapshot is not Live', () => {
  const { store, advance, check } = setup();
  FakeES.all[0].emit('snapshot', snap(100));
  advance(25);
  check();
  FakeES.all[1].emit('snapshot', snap(100)); // hub is hung: same generated_at
  assert.equal(store.get().conn, 'reconnecting');
  advance(25);
  check();
  advance(25);
  check();
  assert.equal(store.get().conn, 'offline', 'three stale checks in a row');
  FakeES.all[FakeES.all.length - 1].emit('snapshot', snap(160)); // the feed moves again
  assert.equal(store.get().conn, 'live');
  assert.equal(store.get().failures, 0);
});
