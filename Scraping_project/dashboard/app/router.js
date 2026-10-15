// Hash routes: #/<tab>/<kind>:<encoded value>/... Pure: no DOM.
import { STAGE_TITLES } from './meta.js';

export const TABS = ['pipeline', 'explore', 'activity', 'health'];
export const TAB_TITLES = { pipeline: 'Pipeline', explore: 'Explore', activity: 'Activity', health: 'Health' };
const KINDS = new Set(['stage', 'path', 'url']);

export function parse(hash) {
  const raw = String(hash || '').replace(/^#/, '').replace(/^\//, '');
  const [tab, ...rest] = raw.split('/');
  if (!TABS.includes(tab)) return { tab: 'pipeline', layers: [] };
  const layers = [];
  for (const seg of rest) {
    const at = seg.indexOf(':');
    const kind = seg.slice(0, at);
    if (at < 1 || !KINDS.has(kind)) break;
    let value;
    try {
      value = decodeURIComponent(seg.slice(at + 1));
    } catch {
      break;
    }
    if (!value) break;
    layers.push({ kind, value });
  }
  return { tab, layers };
}

export function format(route) {
  return `#/${route.tab}` + route.layers.map((l) => `/${l.kind}:${encodeURIComponent(l.value)}`).join('');
}

export const push = (route, layer) => ({ tab: route.tab, layers: [...route.layers, layer] });
export const parent = (route) => ({ tab: route.tab, layers: route.layers.slice(0, -1) });

function hostOf(url) {
  try {
    const u = new URL(url);
    return u.hostname.toLowerCase() || url;
  } catch {
    return url;
  }
}

export function layerTitle(layer) {
  if (layer.kind === 'stage') return STAGE_TITLES[layer.value] || layer.value;
  if (layer.kind === 'path') return layer.value.split('/').filter(Boolean).pop() || layer.value;
  return hostOf(layer.value);
}

export function routeTitle(route) {
  const top = route.layers[route.layers.length - 1];
  return top ? layerTitle(top) : TAB_TITLES[route.tab];
}

export function backLabel(route) {
  return route.layers.length ? routeTitle(parent(route)) : null;
}

export function navDirection(from, to) {
  if (!from || from.tab !== to.tab) return 'tab';
  if (to.layers.length > from.layers.length) return 'push';
  if (to.layers.length < from.layers.length) return 'pop';
  return 'replace';
}
