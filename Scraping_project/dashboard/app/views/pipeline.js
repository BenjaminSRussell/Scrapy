// Pipeline home: how data moves right now, throughput, the newest events, and health.
import { html, useState } from '../lib.js';
import { STAGE_BY_ID } from '../meta.js';
import { healthSummary } from '../state.js';
import { rate, relativeTime } from '../format.js';
import { Section, List, Row, IconTile, Segmented, Loading, Banner } from '../ui/components.js';
import { FlowDiagram } from '../ui/flow.js';
import { AreaChart } from '../ui/chart.js';
import { EventRow } from '../ui/events.js';

const METRICS = [
  { id: 'discovery', label: 'Discovery' },
  { id: 'analysis', label: 'Analysis' },
  { id: 'summaries', label: 'Summaries' },
];

export function ConnectionBanners({ state, now }) {
  const ago = relativeTime(state.lastUpdate, now);
  return html`
    ${state.mode === 'demo' && html`<${Banner} tint="purple" icon="bolt">
      <strong>Demo data.</strong> This is a simulated crawl. Start the server without <code>--demo</code> to watch the real pipeline.</${Banner}>`}
    ${state.conn === 'offline' && html`<${Banner} tint="red" icon="warning" role="alert">
      <strong>Can’t reach the dashboard server.</strong> Showing the last update from ${ago}. Retrying.</${Banner}>`}
    ${state.conn === 'reconnecting' && state.lastUpdate && html`<${Banner} tint="orange" icon="warning">
      <strong>Reconnecting…</strong> Last update ${ago}.</${Banner}>`}
    ${state.pollError && html`<${Banner} tint="orange" icon="warning" role="alert">
      <strong>The feed hit an error:</strong> ${state.pollError}. Numbers may be out of date.</${Banner}>`}`;
}

export function PipelineView({ state, store, route, now }) {
  const [metric, setMetric] = useState('discovery');
  if (!state.stages.length) return html`<${ConnectionBanners} state=${state} now=${now} /><${Loading} label="Connecting to the pipeline" />`;

  const stage = state.stages.find((s) => s.id === metric);
  const health = healthSummary(state.services, state.stages);
  const stale = state.conn !== 'live';

  return html`
    <p class="subtitle">${stale ? `Updated ${relativeTime(state.lastUpdate, now)}` : 'Every dot is a URL moving to its next stage.'}</p>
    <${ConnectionBanners} state=${state} now=${now} />

    <div class=${`card flow-card${stale ? ' stale' : ''}`}>
      <${FlowDiagram} stages=${state.stages} store=${store} route=${route} now=${now} />
    </div>

    <${Section} title="Throughput" big=${true}>
      <div class="card">
        <${Segmented} label="Throughput for" options=${METRICS} value=${metric} onChange=${setMetric} />
        <div class="metric-head">
          <span class="metric-value num">${rate(stage?.rate_per_min)}</span>
          <span class="metric-label">${STAGE_BY_ID[metric].title}, last 30 minutes</span>
        </div>
        <${AreaChart} series=${state.series[metric] || []} tint=${STAGE_BY_ID[metric].tint} label=${`${STAGE_BY_ID[metric].title} rate`} />
      </div>
    </${Section}>

    <${Section} title="Live" big=${true} action=${html`<a class="section-action" href="#/activity">See All</a>`}>
      ${state.events.length
        ? html`<${List} label="Newest events">${state.events.slice(0, 8).map((e) => html`<${EventRow} key=${e.id} event=${e} route=${route} now=${now} />`)}</${List}>`
        : html`<div class="card"><div class="empty"><strong>Nothing yet</strong><span>Events appear here as the pipeline writes them.</span></div></div>`}
    </${Section}>

    <${Section} title="Health" big=${true}>
      <${List}>
        <${Row} icon=${html`<${IconTile} name="health" tint=${health.issues ? 'orange' : 'green'} />`}
          title=${health.label} sub=${state.services.map((s) => s.label).join(', ') || 'Waiting for the first check'}
          href="#/health" />
      </${List}>
    </${Section}>`;
}
