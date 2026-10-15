// The pipeline as a live diagram: stage cards, measured edges, and a dot per URL in motion.
import { html, useEffect, useLayoutEffect, useRef, useState } from '../lib.js';
import { STAGES, STAGE_BY_ID } from '../meta.js';
import { edgeFor } from '../state.js';
import { compact, full, rate, relativeTime, freshnessLabel } from '../format.js';
import { format, push } from '../router.js';
import { edgePath } from '../layout/geometry.js';
import { createBudget } from '../layout/budget.js';
import { StageTile } from './components.js';
import { icons } from './icons.js';
import { useReducedMotion } from './hooks.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const EDGES = [['seeds', 'discovery'], ['discovery', 'analysis'], ['analysis', 'summaries'], ['analysis', 'large_docs']];
const TRAVEL_MS = 1150;
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

function nodeText(s, now) {
  if (!s || s.status === 'missing') return { count: '—', sub: 'No data yet', tone: 'muted' };
  if (s.status === 'error') return { count: '—', sub: 'Can’t read table', tone: 'bad' };
  const active = s.freshness === 'active';
  const sub = active
    ? rate(s.rate_per_min)
    : `${freshnessLabel(s.freshness)} · ${relativeTime(s.last_write, now)}`;
  return { count: compact(s.count), sub, tone: active ? 'good' : 'muted' };
}

function nodeLabel(meta, s, now) {
  if (!s || s.status === 'missing') return `${meta.title}: no data yet. Open details.`;
  if (s.status === 'error') return `${meta.title}: the table can’t be read. Open details.`;
  const parts = [`${meta.title}: ${full(s.count)} URLs`];
  if (s.freshness === 'active') parts.push(`${rate(s.rate_per_min).replace('/min', ' per minute')}`);
  else parts.push(`${freshnessLabel(s.freshness)}, last write ${relativeTime(s.last_write, now)}`);
  if (s.errors) parts.push(`${full(s.errors)} errors`);
  return `${parts.join(', ')}. Open details.`;
}

export function FlowDiagram({ stages, store, route, now }) {
  const wrap = useRef(null);
  const svg = useRef(null);
  const dots = useRef(null);
  const nodes = useRef({});
  const [geo, setGeo] = useState({ w: 0, h: 0, paths: [] });
  const reduced = useReducedMotion();
  const byId = Object.fromEntries(stages.map((s) => [s.id, s]));

  // Measure the cards and draw edges between them; re-measure on any resize.
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return undefined;
    const measure = () => {
      const box = el.getBoundingClientRect();
      const rect = (id) => {
        const n = nodes.current[id];
        if (!n) return null;
        const r = n.getBoundingClientRect();
        return { x: r.left - box.left, y: r.top - box.top, w: r.width, h: r.height };
      };
      const paths = [];
      for (const [a, b] of EDGES) {
        const A = rect(a);
        const B = rect(b);
        if (A && B) paths.push({ key: `${a}>${b}`, to: b, d: edgePath(A, B) });
      }
      setGeo((g) => (g.w === box.width && g.h === box.height && g.paths.every((p, i) => p.d === paths[i]?.d)
        ? g : { w: box.width, h: box.height, paths }));
    };
    measure();
    if (typeof ResizeObserver !== 'function') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // One dot per fresh event, along its edge; the target card bumps on arrival.
  useEffect(() => {
    if (reduced || !store) return undefined;
    const budget = createBudget();
    const live = [];
    let raf = 0;
    const bump = (id) => {
      const n = nodes.current[id];
      if (!n) return;
      n.classList.remove('bump');
      void n.offsetWidth; // restart the animation
      n.classList.add('bump');
    };
    const frame = (t) => {
      for (let i = live.length - 1; i >= 0; i--) {
        const p = live[i];
        const k = (t - p.start) / TRAVEL_MS;
        if (k < 0) continue;
        if (k >= 1) {
          p.el.remove();
          live.splice(i, 1);
          budget.release();
          bump(p.to);
          continue;
        }
        const pt = p.path.getPointAtLength(p.len * ease(k));
        p.el.setAttribute('cx', pt.x.toFixed(1));
        p.el.setAttribute('cy', pt.y.toFixed(1));
        p.el.setAttribute('opacity', String(Math.min(1, k * 8, (1 - k) * 6)));
      }
      raf = live.length ? requestAnimationFrame(frame) : 0;
    };
    const off = store.onFresh((events) => {
      if (document.hidden || !svg.current || !dots.current) return;
      const moving = events.filter((e) => edgeFor(e));
      const spacing = Math.min(180, 900 / Math.max(1, moving.length));
      const start = performance.now();
      moving.forEach((ev, i) => {
        const [from, to] = edgeFor(ev);
        const key = `${from}>${to}`;
        const path = svg.current.querySelector(`path[data-edge="${key}"]`);
        if (!path || !budget.take(key)) return;
        const c = document.createElementNS(SVG_NS, 'circle');
        c.setAttribute('r', '4');
        c.setAttribute('opacity', '0');
        c.setAttribute('class', `particle tint-${STAGE_BY_ID[to].tint}`);
        dots.current.appendChild(c);
        live.push({ el: c, path, len: path.getTotalLength(), start: start + i * spacing, to });
      });
      if (!raf && live.length) raf = requestAnimationFrame(frame);
    });
    return () => {
      off();
      cancelAnimationFrame(raf);
      live.forEach((p) => p.el.remove());
    };
  }, [reduced, store]);

  return html`<div class="flow" ref=${wrap} role="group" aria-label="Pipeline stages">
    <svg class="flow-edges" ref=${svg} width=${geo.w} height=${geo.h} viewBox=${`0 0 ${geo.w || 1} ${geo.h || 1}`} aria-hidden="true" focusable="false">
      ${geo.paths.map((p) => html`<path data-edge=${p.key} class=${`edge tint-${STAGE_BY_ID[p.to].tint}`} d=${p.d} />`)}
      <g ref=${dots}></g>
    </svg>
    ${STAGES.map((meta) => {
      const s = byId[meta.id];
      const t = nodeText(s, now);
      return html`<a class=${`flow-node tint-${meta.tint}`} data-stage=${meta.id}
          href=${format(push(route, { kind: 'stage', value: meta.id }))}
          ref=${(el) => { nodes.current[meta.id] = el; }} aria-label=${nodeLabel(meta, s, now)}>
        <span class="flow-head"><${StageTile} stageId=${meta.id} /><span class="flow-title">${meta.title}</span>${icons.chevronRight({ cls: 'chev' })}</span>
        <span class="flow-count num">${t.count}</span>
        <span class=${`flow-sub ${t.tone}`}>${t.sub}${s && s.errors ? html` · <span class="bad">${compact(s.errors)} errors</span>` : ''}</span>
      </a>`;
    })}
  </div>`;
}
