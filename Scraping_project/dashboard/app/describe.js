// What a stage card says about a stage. Pure: no DOM.
import { compact, full, rate, relativeTime, freshnessLabel } from './format.js';

export function stageCard(s, now) {
  if (!s || s.status === 'missing') return { count: '—', sub: 'No data yet', tone: 'muted' };
  if (s.status === 'error') return { count: '—', sub: 'Can’t read table', tone: 'bad' };
  const count = compact(s.count);
  if (s.freshness === 'active' && s.rate_per_min > 0) return { count, sub: rate(s.rate_per_min), tone: 'good' };
  return { count, sub: `${freshnessLabel(s.freshness)} · ${relativeTime(s.last_write, now)}`, tone: 'muted' };
}

export function stageLabel(meta, s, now) {
  if (!s || s.status === 'missing') return `${meta.title}: no data yet. Open details.`;
  if (s.status === 'error') return `${meta.title}: the table can’t be read. Open details.`;
  const parts = [`${meta.title}: ${full(s.count)} URLs`];
  if (s.freshness === 'active' && s.rate_per_min > 0) parts.push(rate(s.rate_per_min).replace('/min', ' per minute'));
  else parts.push(`${freshnessLabel(s.freshness)}, last write ${relativeTime(s.last_write, now)}`);
  if (s.errors) parts.push(`${full(s.errors)} errors`);
  return `${parts.join(', ')}. Open details.`;
}
