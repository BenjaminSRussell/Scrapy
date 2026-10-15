// One stage: totals, its rate over time, what it has been doing, and its newest rows (live).
import { html, useMemo } from '../lib.js';
import { STAGE_BY_ID } from '../meta.js';
import { stagePath } from '../api.js';
import { full, rate, relativeTime, formatValue, freshnessLabel } from '../format.js';
import { format, parent } from '../router.js';
import { Section, List, Row, Tile, Loading, ErrorCard, Banner } from '../ui/components.js';
import { AreaChart } from '../ui/chart.js';
import { BarList } from '../ui/bars.js';
import { EventRow } from '../ui/events.js';
import { useApi, useThrottled } from '../ui/hooks.js';
import { ConnectionBanners } from './pipeline.js';

const RECENT_SHOWN = 25;
const KIND = { seeds: 'seeded', discovery: 'discovered', analysis: 'analyzed', summaries: 'summarized', large_docs: 'large_doc' };

function Breakdown({ b, tint }) {
  if (b.kind === 'bars') {
    return html`<${Section} title=${b.title}><div class="card"><${BarList} items=${b.items} tint=${tint} label=${b.title} /></div></${Section}>`;
  }
  return html`<${Section} title=${b.title}><${List}>${b.items.map((i) => html`<${Row} title=${i.label} value=${formatValue(i.value, i.format)} />`)}</${List}></${Section}>`;
}

export function StageView({ state, route, now }) {
  const id = route.layers[route.layers.length - 1].value;
  const meta = STAGE_BY_ID[id];
  const live = state.stages.find((s) => s.id === id);
  const refreshKey = useThrottled(live?.version ?? null, 5000);
  const { data, error, loading, retry } = useApi(meta ? stagePath(id) : null, refreshKey);

  // Live events for this stage go on top of the server's newest rows, without duplicates.
  const recent = useMemo(() => {
    if (!meta) return [];
    const liveRows = state.events.filter((e) => e.stage === id && e.kind !== 'error');
    const seen = new Set(liveRows.map((e) => `${e.url}|${e.t}`));
    const rows = (data?.recent || [])
      .filter((r) => !seen.has(`${r.url}|${r.t}`))
      .map((r, i) => ({ id: `api:${id}:${i}`, stage: id, kind: KIND[id], url: r.url, t: r.t, fields: r.fields }));
    return [...liveRows, ...rows].slice(0, RECENT_SHOWN);
  }, [state.events, data, id]);

  if (!meta) return html`<${ErrorCard} error=${{ status: 404 }} backHref=${format(parent(route))} />`;

  return html`
    <p class="subtitle">${meta.blurb}</p>
    <${ConnectionBanners} state=${state} now=${now} />
    ${live?.status === 'missing' && html`<${Banner} tint="gray" icon="info">The <code>${live.table}</code> table doesn’t exist yet. It appears here once the stage writes its first row.</${Banner}>`}
    ${live?.status === 'error' && html`<${Banner} tint="red" icon="warning" role="alert"><strong>Can’t read <code>${live.table}</code>.</strong> ${live.error}</${Banner}>`}

    <div class="tiles">
      <${Tile} label="Total" value=${full(live?.count)} />
      <${Tile} label="Per minute" value=${live?.rate_per_min == null ? '—' : rate(live.rate_per_min).replace('/min', '')} />
      <${Tile} label="Last write" value=${relativeTime(live?.last_write, now)} />
      ${id === 'discovery' && live?.errors != null && html`<${Tile} label="Errors" value=${full(live.errors)} />`}
    </div>

    <${Section} title="Rate">
      <div class="card">
        <div class="metric-head"><span class="metric-value num">${rate(live?.rate_per_min)}</span>
          <span class="metric-label">${freshnessLabel(live?.freshness)}, last 30 minutes</span></div>
        <${AreaChart} series=${state.series[id] || []} tint=${meta.tint} label=${`${meta.title} rate`} />
      </div>
    </${Section}>

    ${error && !data && html`<${ErrorCard} error=${error} onRetry=${retry} />`}
    ${loading && !data && !error && html`<${Loading} label=${`Loading ${meta.title}`} />`}
    ${data && data.breakdowns.map((b) => html`<${Breakdown} b=${b} tint=${meta.tint} />`)}

    <${Section} title="Recent" big=${true}>
      ${recent.length
        ? html`<${List} label=${`Newest ${meta.title} rows`}>${recent.map((e) => html`<${EventRow} key=${e.id} event=${e} route=${route} now=${now} />`)}</${List}>`
        : html`<div class="card"><div class="empty"><strong>No rows yet</strong><span>New rows from this stage appear here as they land.</span></div></div>`}
    </${Section}>`;
}
