// Shared building blocks: layer chrome, inset grouped lists, status, controls.
import { html, useEffect, useRef, useState, useId } from '../lib.js';
import { icons, glyph, STAGE_GLYPH } from './icons.js';
import { STAGE_BY_ID } from '../meta.js';
import { freshnessLabel } from '../format.js';

// ---- layer chrome: nav bar + collapsing large title ----
export function Layer({ title, back, trailing, children }) {
  const [scrolled, setScrolled] = useState(false);
  const titleRef = useRef(null);
  useEffect(() => {
    const el = titleRef.current;
    if (!el || typeof IntersectionObserver !== 'function') return undefined;
    const io = new IntersectionObserver(([entry]) => setScrolled(!entry.isIntersecting), {
      rootMargin: '-52px 0px 0px 0px',
      threshold: 0,
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return html`
    <header class="navbar" data-scrolled=${String(scrolled)}>
      <div class="navbar-row">
        <div class="navbar-leading">
          ${back && html`<a class="back" href=${back.href} onClick=${back.onClick} aria-label=${`Back to ${back.label}`}>
            ${icons.chevronLeft()}<span>${back.label}</span></a>`}
        </div>
        <div class="navbar-title" aria-hidden="true">${title}</div>
        <div class="navbar-trailing">${trailing}</div>
      </div>
    </header>
    <div class="layer-content">
      <h1 class="large-title" tabindex="-1" ref=${titleRef}>${title}</h1>
      ${children}
    </div>`;
}

// ---- sections and inset grouped lists ----
export function Section({ title, big, action, footer, children, label }) {
  const id = useId();
  return html`
    <section class="section" aria-labelledby=${title ? id : undefined} aria-label=${title ? undefined : label}>
      ${(title || action) && html`<div class="section-head">
        ${title && html`<h2 class=${`section-title${big ? ' big' : ''}`} id=${id}>${title}</h2>`}
        ${action}
      </div>`}
      ${children}
      ${footer && html`<p class="section-foot">${footer}</p>`}
    </section>`;
}

export const List = ({ children, label }) => html`<ul class="list" aria-label=${label}>${children}</ul>`;

export function Row({ href, onClick, icon, title, sub, value, strong, chevron, label, wrap, children, target, rel }) {
  const Tag = href ? 'a' : onClick ? 'button' : 'div';
  const showChevron = chevron ?? Boolean(href && !target);
  return html`<li>
    <${Tag} class=${`row${icon ? ' has-icon' : ''}`} href=${href} onClick=${onClick} aria-label=${label}
      type=${Tag === 'button' ? 'button' : undefined} target=${target} rel=${rel}>
      ${icon}
      <div class="row-body">
        <div class=${`row-title${wrap ? ' wrap' : ''}`}>${title}</div>
        ${sub != null && sub !== '' && html`<div class="row-sub">${sub}</div>`}
        ${children}
      </div>
      ${value != null && value !== '' && html`<div class=${`row-value${strong ? ' strong' : ''}`}>${value}</div>`}
      ${showChevron && icons.chevronRight({ cls: 'chev' })}
      ${target && icons.external({ cls: 'chev' })}
    </${Tag}>
  </li>`;
}

export function IconTile({ name, tint = 'gray', large }) {
  return html`<span class=${`icon-tile tint-${tint}${large ? ' lg' : ''}`} aria-hidden="true">${glyph(name)}</span>`;
}

export function StageTile({ stageId, large }) {
  const meta = STAGE_BY_ID[stageId];
  return html`<${IconTile} name=${STAGE_GLYPH[stageId]} tint=${meta ? meta.tint : 'gray'} large=${large} />`;
}

// ---- status ----
const CONN = {
  live: { cls: 'live', text: 'Live' },
  demo: { cls: 'demo', text: 'Demo' },
  connecting: { cls: 'reconnecting', text: 'Connecting' },
  reconnecting: { cls: 'reconnecting', text: 'Reconnecting' },
  offline: { cls: 'offline', text: 'Offline' },
};

export function connState(state) {
  if (state.conn === 'live' && state.mode === 'demo') return CONN.demo;
  return CONN[state.conn] || CONN.connecting;
}

export function ConnPill({ state }) {
  const c = connState(state);
  return html`<span class=${`pill ${c.cls}`} role="status" aria-label=${`Connection: ${c.text}`}>
    <span class="dot" aria-hidden="true"></span>${c.text}</span>`;
}

const SERVICE_TEXT = { ok: 'Normal', warn: 'Degraded', down: 'Down', unknown: 'Unknown' };
export const serviceText = (status) => SERVICE_TEXT[status] || 'Unknown';

export function StatusLabel({ status, text }) {
  return html`<span class="status-label"><span class=${`dot ${status}`} aria-hidden="true"></span>${text ?? serviceText(status)}</span>`;
}

const FRESH_DOT = { active: 'ok', idle: 'warn', stale: 'unknown', none: 'unknown' };
export function Freshness({ freshness }) {
  return html`<${StatusLabel} status=${FRESH_DOT[freshness] || 'unknown'} text=${freshnessLabel(freshness)} />`;
}

// ---- controls ----
export function Segmented({ label, options, value, onChange }) {
  const onKey = (e) => {
    const i = options.findIndex((o) => o.id === value);
    let next = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = options[(i + 1) % options.length];
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = options[(i - 1 + options.length) % options.length];
    if (!next) return;
    e.preventDefault();
    onChange(next.id);
    requestAnimationFrame(() => e.currentTarget?.querySelector('[aria-checked="true"]')?.focus());
  };
  return html`<div class="segmented" role="radiogroup" aria-label=${label} onKeyDown=${onKey}>
    ${options.map((o) => html`<button type="button" role="radio" class="segment" aria-checked=${String(o.id === value)}
      tabindex=${o.id === value ? 0 : -1} onClick=${() => onChange(o.id)}>${o.label}</button>`)}
  </div>`;
}

export function SearchField({ value, onInput, placeholder = 'Search', label }) {
  const ref = useRef(null);
  return html`<div class="search" role="search">
    ${icons.search({ cls: 'mag' })}
    <input ref=${ref} type="search" value=${value} placeholder=${placeholder} aria-label=${label || placeholder}
      autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search"
      onInput=${(e) => onInput(e.currentTarget.value)}
      onKeyDown=${(e) => { if (e.key === 'Escape' && value) { e.preventDefault(); onInput(''); } }} />
    ${value && html`<button type="button" class="clear" aria-label="Clear search"
      onClick=${() => { onInput(''); ref.current?.focus(); }}>${icons.clear()}</button>`}
  </div>`;
}

export function Skeleton({ lines = 3, block }) {
  return html`<div aria-hidden="true">
    ${block && html`<div class="skeleton block"></div>`}
    ${Array.from({ length: lines }, (_, i) => html`<div class="skeleton line" style=${`width:${90 - i * 17}%`}></div>`)}
  </div>`;
}

export function Loading({ label = 'Loading' }) {
  return html`<div class="card" role="status" aria-live="polite"><span class="sr-only">${label}</span><${Skeleton} lines=${4} /></div>`;
}

export function ErrorCard({ error, onRetry, backHref }) {
  const notFound = error && error.status === 404;
  return html`<div class="card" role="alert">
    <div class="empty">
      ${icons.warning()}
      <strong>${notFound ? 'Not found' : 'Couldn’t load this'}</strong>
      <span>${notFound ? 'It may have been removed from the lake, or it was never crawled.' : String(error?.message || error)}</span>
      <div class="btn-row">
        ${!notFound && onRetry && html`<button type="button" class="btn" onClick=${onRetry}>Try Again</button>`}
        ${backHref && html`<a class="btn" href=${backHref}>Go Back</a>`}
      </div>
    </div>
  </div>`;
}

export function Banner({ tint = 'blue', icon = 'info', children, role }) {
  return html`<div class=${`banner tint-${tint}`} role=${role}>${glyph(icon)}<div>${children}</div></div>`;
}

export function Tile({ label, value, unit }) {
  return html`<div class="tile"><div class="tile-label">${label}</div>
    <div class="tile-value">${value}${unit && html`<small>${unit}</small>`}</div></div>`;
}
