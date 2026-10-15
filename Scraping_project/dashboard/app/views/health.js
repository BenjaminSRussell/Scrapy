// Is everything working? Only what was actually checked; never an assumed "Healthy".
import { html } from '../lib.js';
import { STAGES, SERVICE_ORDER } from '../meta.js';
import { relativeTime, full } from '../format.js';
import { format, push } from '../router.js';
import { Section, List, Row, IconTile, StageTile, StatusLabel, Freshness, connState } from '../ui/components.js';

const SERVICE_ICON = { redis: 'database', delta: 'table', metrics: 'gauge' };
const CONN_STATUS = { live: 'ok', demo: 'ok', reconnecting: 'warn', offline: 'down' };

export function HealthView({ state, route, now }) {
  const conn = connState(state);
  const services = [...state.services].sort((a, b) => {
    const ia = SERVICE_ORDER.indexOf(a.id);
    const ib = SERVICE_ORDER.indexOf(b.id);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  const byId = Object.fromEntries(state.stages.map((s) => [s.id, s]));
  const grafana = typeof window !== 'undefined' ? window.__CC_GRAFANA_URL__ : null;
  const version = typeof window !== 'undefined' ? window.__CC_VERSION__ : null;

  return html`
    <p class="subtitle">${state.lastUpdate ? `Checked ${relativeTime(state.lastUpdate, now)}` : 'Waiting for the first check'}</p>

    <${Section} title="This dashboard">
      <${List}>
        <${Row} icon=${html`<${IconTile} name="bolt" tint="blue" />`} title="Live stream"
          sub=${state.lastUpdate ? `Last update ${relativeTime(state.lastUpdate, now)}` : 'Connecting…'}
          value=${html`<${StatusLabel} status=${CONN_STATUS[conn.cls] || 'warn'} text=${conn.text} />`} />
        <${Row} icon=${html`<${IconTile} name="info" tint="gray" />`} title="Data source"
          value=${state.mode === 'demo' ? 'Simulated (demo)' : state.mode === 'live' ? 'Delta Lake + Redis' : '—'} />
        ${state.pollError && html`<${Row} icon=${html`<${IconTile} name="warning" tint="orange" />`} title="Last poll failed" sub=${state.pollError} wrap=${true} />`}
        ${version && html`<${Row} title="Version" value=${version} />`}
      </${List}>
    </${Section}>

    <${Section} title="Services">
      ${services.length
        ? html`<${List}>${services.map((s) => html`<${Row} key=${s.id} icon=${html`<${IconTile} name=${SERVICE_ICON[s.id] || 'info'} tint="gray" />`}
            title=${s.label} sub=${s.detail} value=${html`<${StatusLabel} status=${s.status} />`}
            label=${`${s.label}: ${s.status}. ${s.detail}`}>
            ${(s.data?.queues || []).map((q) => html`<div class="row-sub queue"><code>${q.key}</code> · ${full(q.depth)} waiting</div>`)}
          </${Row}>`)}</${List}>`
        : html`<div class="card"><div class="empty"><span>No service checks reported yet.</span></div></div>`}
    </${Section}>

    <${Section} title="Stages" footer="Workers don’t send heartbeats, so a stage’s health is judged by its newest write: active under 2 minutes, idle under 30, stale after that.">
      <${List}>${STAGES.map((meta) => {
        const s = byId[meta.id];
        const sub = !s || s.status === 'missing' ? 'Table not created yet'
          : s.status === 'error' ? `Can’t read ${s.table}: ${s.error}`
            : `Last write ${relativeTime(s.last_write, now)}`;
        return html`<${Row} key=${meta.id} icon=${html`<${StageTile} stageId=${meta.id} />`} title=${meta.title} sub=${sub}
          value=${s && s.status === 'error' ? html`<${StatusLabel} status="down" text="Unreadable" />` : html`<${Freshness} freshness=${s ? s.freshness : 'none'} />`}
          href=${format(push(route, { kind: 'stage', value: meta.id }))} />`;
      })}</${List}>
    </${Section}>

    <${Section} title="More">
      <${List}>
        ${grafana && html`<${Row} icon=${html`<${IconTile} name="gauge" tint="orange" />`} title="Open Grafana" sub="History, alerts and custom queries" href=${grafana} target="_blank" rel="noopener noreferrer" />`}
        <${Row} icon=${html`<${IconTile} name="doc" tint="gray" />`} title="Raw snapshot" sub="The JSON this page is drawn from" href="api/snapshot" target="_blank" rel="noopener noreferrer" />
      </${List}>
    </${Section}>`;
}
