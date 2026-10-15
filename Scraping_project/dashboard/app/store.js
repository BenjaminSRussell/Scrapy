// Live state from /api/stream (server-sent events), with honest connection status.
//
// "Live" means the server's data is moving, not merely that a socket is open: a
// watchdog reopens the stream when generated_at has not advanced for
// max(3 x poll interval, 20 s), which catches half-open mobile connections and a
// hung feed that keeps replaying the same snapshot.
import { initialState, applySnapshot, applyTick, freshEvents } from './state.js';

const OFFLINE_AFTER = 3;
const STALE_MIN_S = 20;
const WATCHDOG_MS = 5000;

export function createStore({
  url = '/api/stream',
  EventSourceImpl = globalThis.EventSource,
  clock = () => Date.now() / 1000,
  setIntervalImpl = (fn, ms) => setInterval(fn, ms),
  setTimeoutImpl = (fn, ms) => setTimeout(fn, ms),
  clearTimeoutImpl = (id) => clearTimeout(id),
} = {}) {
  let state = initialState();
  const listeners = new Set();
  const freshListeners = new Set();
  let es = null;
  let retryTimer = null;
  let backoff = 2000;
  let interval = 2;
  let lastAdvance = null; // client time when generated_at last moved

  const emit = () => listeners.forEach((fn) => fn(state));
  const set = (patch) => {
    state = { ...state, ...patch };
    emit();
  };

  // Data that moved means the stream is healthy; a replay of frozen data does not.
  function connFor(payload) {
    if (payload.generated_at !== state.generatedAt || state.conn === 'connecting') {
      lastAdvance = clock();
      backoff = 2000;
      return { conn: 'live', failures: 0 };
    }
    return { conn: state.conn, failures: state.failures };
  }

  function onSnapshot(e) {
    const snap = JSON.parse(e.data);
    if (snap.interval > 0) interval = snap.interval;
    const status = connFor(snap);
    state = { ...applySnapshot(state, snap, clock()), ...status };
    emit();
  }

  function onTick(e) {
    const tick = JSON.parse(e.data);
    const fresh = freshEvents(state, tick);
    const status = connFor(tick);
    state = { ...applyTick(state, tick, clock()), ...status };
    emit();
    if (fresh.length) freshListeners.forEach((fn) => fn(fresh));
  }

  function fail() {
    const failures = state.failures + 1;
    set({ failures, conn: failures >= OFFLINE_AFTER ? 'offline' : 'reconnecting' });
  }

  function onError() {
    fail();
    // A non-200 (401, 429, 503) closes the source for good; retry it ourselves.
    if (es && es.readyState === 2) {
      es.close();
      es = null;
      clearTimeoutImpl(retryTimer);
      retryTimer = setTimeoutImpl(connect, backoff);
      backoff = Math.min(backoff * 2, 30000);
    }
  }

  function connect() {
    if (!EventSourceImpl) {
      set({ conn: 'offline' });
      return;
    }
    es = new EventSourceImpl(url);
    es.addEventListener('snapshot', onSnapshot);
    es.addEventListener('tick', onTick);
    es.addEventListener('error', onError);
  }

  function watchdog() {
    if (lastAdvance == null || state.conn === 'connecting') return;
    if (clock() - lastAdvance <= Math.max(STALE_MIN_S, 3 * interval)) return;
    lastAdvance = clock(); // one reconnect per stale window
    fail();
    if (es) es.close();
    es = null;
    connect();
  }

  connect();
  setIntervalImpl(watchdog, WATCHDOG_MS);

  return {
    get: () => state,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    onFresh(fn) {
      freshListeners.add(fn);
      return () => freshListeners.delete(fn);
    },
    close() {
      clearTimeoutImpl(retryTimer);
      if (es) es.close();
    },
  };
}
