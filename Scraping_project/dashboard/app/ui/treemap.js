// Squarified treemap of one Explore level. Pointer shortcut only: the list below is the
// accessible way through, so tiles are hidden from assistive tech and the tab order.
import { html, useLayoutEffect, useRef, useState } from '../lib.js';
import { squarify } from '../layout/squarify.js';
import { compact } from '../format.js';

export const PALETTE = ['blue', 'indigo', 'purple', 'teal', 'green', 'orange', 'red'];
const MAX_TILES = 40;

export function Treemap({ items, hrefFor, tintFor, height = 240 }) {
  const box = useRef(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver !== 'function') return undefined;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const tiles = width ? squarify(items.slice(0, MAX_TILES).map((i, n) => ({ ...i, n, value: i.count })), 0, 0, width, height) : [];
  return html`<div class="treemap" ref=${box} style=${`height:${height}px`} aria-hidden="true">
    ${tiles.map((t) => {
      const roomy = t.w > 64 && t.h > 40;
      return html`<a class=${`tm-tile tint-${tintFor(t, t.n)}`} href=${hrefFor(t)} tabindex="-1"
          style=${`left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px`}>
        ${roomy && html`<span class="tm-name">${t.segment}</span><span class="tm-count num">${compact(t.count)}</span>`}
      </a>`;
    })}
  </div>`;
}
