// Line glyphs in the spirit of SF Symbols. Decorative: always aria-hidden.
import { html } from '../lib.js';

const stroke = (body, { cls = '', w = 1.8 } = {}) => html`
  <svg class=${cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width=${w}
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
const solid = (body, { cls = '' } = {}) => html`
  <svg class=${cls} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false">${body}</svg>`;

export const icons = {
  pipeline: (o) => stroke(html`<circle cx="5" cy="12" r="2.4"/><circle cx="19" cy="6" r="2.4"/><circle cx="19" cy="18" r="2.4"/>
    <path d="M7.4 12h3.1c2.3 0 2.9-6 6.1-6"/><path d="M10.5 12c2.3 0 2.9 6 6.1 6"/>`, o),
  explore: (o) => stroke(html`<rect x="3" y="3" width="10" height="11" rx="2"/><rect x="15" y="3" width="6" height="6" rx="1.6"/>
    <rect x="15" y="11" width="6" height="10" rx="1.6"/><rect x="3" y="16" width="10" height="5" rx="1.6"/>`, o),
  activity: (o) => stroke(html`<path d="M2.5 12h4l2.5-6 4 12 2.6-6H21.5"/>`, o),
  health: (o) => stroke(html`<path d="M12 20s-7.5-4.4-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10C19.5 15.6 12 20 12 20Z"/>
    <path d="M7.5 12.5h2.6l1.2-2 1.8 4 1.2-2h2.2"/>`, o),
  seed: (o) => stroke(html`<path d="M12 21v-7"/><path d="M12 14C7 14 5 10.5 5 6c4.7 0 7 2.6 7 8Z"/><path d="M12 12c0-4.4 2.3-7 7-7 0 4.5-2.1 7-7 7Z"/>`, o),
  search: (o) => stroke(html`<circle cx="10.5" cy="10.5" r="6.2"/><path d="m15.2 15.2 5.3 5.3"/>`, o),
  doc: (o) => stroke(html`<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M8.5 13h7M8.5 16.5h5"/>`, o),
  quote: (o) => stroke(html`<path d="M4 6h16M4 10.5h16M4 15h10"/><path d="m16 19 2.5-2.5L21 19"/>`, o),
  stack: (o) => stroke(html`<rect x="7" y="3" width="13" height="15" rx="2"/><path d="M4 7v12a2 2 0 0 0 2 2h10"/><path d="M10.5 8.5h6M10.5 12h6"/>`, o),
  chevronLeft: (o) => stroke(html`<path d="M15.5 3.5 7 12l8.5 8.5"/>`, { w: 2.6, ...o }),
  chevronRight: (o) => stroke(html`<path d="m9 4.5 7.5 7.5L9 19.5"/>`, { w: 2.6, ...o }),
  share: (o) => stroke(html`<path d="M12 15V3"/><path d="m7.5 7.5 4.5-4.5 4.5 4.5"/><path d="M8 10H6.5A1.5 1.5 0 0 0 5 11.5v8A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5v-8a1.5 1.5 0 0 0-1.5-1.5H16"/>`, o),
  external: (o) => stroke(html`<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>`, o),
  pause: (o) => solid(html`<rect x="6" y="4.5" width="4" height="15" rx="1.2"/><rect x="14" y="4.5" width="4" height="15" rx="1.2"/>`, o),
  play: (o) => solid(html`<path d="M7 4.8v14.4a1 1 0 0 0 1.5.86l12-7.2a1 1 0 0 0 0-1.72l-12-7.2A1 1 0 0 0 7 4.8Z"/>`, o),
  clear: (o) => solid(html`<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm3.5 12.1-1.4 1.4L12 13.4l-2.1 2.1-1.4-1.4 2.1-2.1-2.1-2.1 1.4-1.4 2.1 2.1 2.1-2.1 1.4 1.4-2.1 2.1Z"/>`, o),
  warning: (o) => stroke(html`<path d="M10.3 3.9 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9.5v4"/><path d="M12 17h.01"/>`, o),
  info: (o) => stroke(html`<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><path d="M12 7.5h.01"/>`, o),
  check: (o) => solid(html`<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm-1.3 14.2-4.2-4.2 1.4-1.4 2.8 2.8 5.6-5.6 1.4 1.4-7 7Z"/>`, o),
  waiting: (o) => stroke(html`<circle cx="12" cy="12" r="8.5" stroke-dasharray="2.6 3.1"/>`, o),
  skipped: (o) => stroke(html`<circle cx="12" cy="12" r="8.5"/><path d="M8 12h8"/>`, o),
  link: (o) => stroke(html`<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/>`, o),
  database: (o) => stroke(html`<ellipse cx="12" cy="5.5" rx="7.5" ry="2.8"/><path d="M4.5 5.5v13c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-13"/><path d="M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8"/>`, o),
  table: (o) => stroke(html`<rect x="3.5" y="4" width="17" height="16" rx="2.5"/><path d="M3.5 9.5h17M3.5 15h17M9.5 9.5V20"/>`, o),
  gauge: (o) => stroke(html`<path d="M4.2 17.5a8.5 8.5 0 1 1 15.6 0"/><path d="m12 13 3.5-4"/><circle cx="12" cy="13.4" r="1.4"/>`, o),
  bolt: (o) => solid(html`<path d="M13.5 2 4.8 13.4a.7.7 0 0 0 .55 1.1H11l-1 7.5 8.7-11.4a.7.7 0 0 0-.55-1.1H12.5Z"/>`, o),
  globe: (o) => stroke(html`<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.5 2.6 3.7 5.6 3.7 9S14.5 18.4 12 21c-2.5-2.6-3.7-5.6-3.7-9S9.5 5.6 12 3Z"/>`, o),
  folder: (o) => stroke(html`<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h3.7l2 2.2h7.3a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z"/>`, o),
  copy: (o) => stroke(html`<rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>`, o),
};

export const STAGE_GLYPH = { seeds: 'seed', discovery: 'search', analysis: 'doc', summaries: 'quote', large_docs: 'stack' };
export const glyph = (name, o) => (icons[name] || icons.info)(o);
