// Horizontal bars with the number as text beside each one (the bar itself is decoration).
import { html } from '../lib.js';
import { full } from '../format.js';

export function BarList({ items, tint = 'blue', label }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  const total = items.reduce((a, i) => a + i.value, 0);
  return html`<ul class=${`bars tint-${tint}`} aria-label=${label}>
    ${items.map((i) => html`<li class="bar">
      <span class="bar-label">${i.label}</span>
      <span class="bar-value num">${full(i.value)}<span class="sr-only">${total ? `, ${Math.round((100 * i.value) / total)}%` : ''}</span></span>
      <span class="bar-track" aria-hidden="true"><span class="bar-fill" style=${`width:${(100 * i.value) / max}%`}></span></span>
    </li>`)}
  </ul>`;
}
