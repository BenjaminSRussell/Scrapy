// Pure reducers over the live state. Events are kept newest first.
import { EDGE_FOR_KIND, FILTERS } from './meta.js';

export const MAX_EVENTS = 500;
export const MAX_SERIES = 180;

export function initialState() {
  return {
    conn: 'connecting', // connecting | live | reconnecting | offline
    failures: 0,
    mode: null,
    generatedAt: null,
    lastUpdate: null,
    pollError: null,
    stages: [],
    edges: [],
    services: [],
    series: {},
    events: [],
  };
}

function mergeEvents(existing, incomingNewestFirst) {
  const seen = new Set();
  const out = [];
  for (const e of [...incomingNewestFirst, ...existing]) {
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    out.push(e);
    if (out.length >= MAX_EVENTS) break;
  }
  return out;
}

export function applySnapshot(state, snap, now) {
  return {
    ...state,
    mode: snap.mode,
    generatedAt: snap.generated_at,
    lastUpdate: now,
    pollError: snap.poll_error ?? null,
    stages: snap.stages || [],
    edges: snap.edges || [],
    services: snap.services || [],
    series: { ...(snap.series || {}) },
    events: mergeEvents([], snap.recent || []),
  };
}

export function applyTick(state, tick, now) {
  const series = { ...state.series };
  for (const [id, point] of Object.entries(tick.series_point || {})) {
    const arr = (series[id] || []).slice();
    const last = arr[arr.length - 1];
    if (last && last[0] === point[0]) arr[arr.length - 1] = point;
    else arr.push(point);
    series[id] = arr.slice(-MAX_SERIES);
  }
  const byId = Object.fromEntries((tick.stages || []).map((s) => [s.id, s]));
  return {
    ...state,
    generatedAt: tick.generated_at,
    lastUpdate: now,
    pollError: tick.poll_error ?? null,
    stages: state.stages.length ? state.stages.map((s) => byId[s.id] || s) : tick.stages || [],
    services: tick.services && tick.services.length ? tick.services : state.services,
    series,
    events: mergeEvents(state.events, [...(tick.events || [])].reverse()),
  };
}

// Events in a tick that should animate: not replayed and not already listed.
export function freshEvents(state, tick) {
  const seen = new Set(state.events.map((e) => e.id));
  return (tick.events || []).filter((e) => !e.replay && !seen.has(e.id));
}

export function filterEvents(events, filterId, query) {
  const kinds = FILTERS.find((f) => f.id === filterId)?.kinds;
  const q = String(query || '').trim().toLowerCase();
  return events.filter((e) => (!kinds || kinds.includes(e.kind)) && (!q || e.url.toLowerCase().includes(q)));
}

export function healthSummary(services, stages) {
  const issues = services.filter((s) => s.status === 'down' || s.status === 'warn').length
    + stages.filter((s) => s.status === 'error').length;
  return { issues, label: issues ? `${issues} issue${issues === 1 ? '' : 's'}` : 'All systems normal' };
}

export const edgeFor = (event) => EDGE_FOR_KIND[event.kind] || null;
