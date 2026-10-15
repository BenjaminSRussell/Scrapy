// Live state from /api/stream (server-sent events), with honest connection status.
import { initialState, applySnapshot, applyTick, freshEvents } from './state.js';

const OFFLINE_AFTER = 3;

export function createStore({ url = '/api/stream', EventSourceImpl = globalThis.EventSource, clock = () => Date.now() / 1000 } = {}) {
  let state = initialState();
  const listeners = new Set();
  const freshListeners = new Set();
  let es = null;
  let retryTimer = null;
  let backoff = 2000;

  const emit = () => listeners.forEach((fn) => fn(state));
  const set = (patch) => {
    state = { ...state, ...patch };
    emit();
  };

  function onSnapshot(e) {
    state = { ...applySnapshot(state, JSON.parse(e.data), clock()), conn: 'live', failures: 0 };
    backoff = 2000;
    emit();
  }

  function onTick(e) {
    const tick = JSON.parse(e.data);
    const fresh = freshEvents(state, tick);
    state = { ...applyTick(state, tick, clock()), conn: 'live', failures: 0 };
    emit();
    if (fresh.length) freshListeners.forEach((fn) => fn(fresh));
  }

  function onError() {
    const failures = state.failures + 1;
    set({ failures, conn: failures >= OFFLINE_AFTER ? 'offline' : 'reconnecting' });
    // A non-200 (401, 429, 503) closes the source for good; retry it ourselves.
    if (es && es.readyState === 2) {
      es.close();
      es = null;
      clearTimeout(retryTimer);
      retryTimer = setTimeout(connect, backoff);
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

  connect();

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
      clearTimeout(retryTimer);
      if (es) es.close();
    },
  };
}
