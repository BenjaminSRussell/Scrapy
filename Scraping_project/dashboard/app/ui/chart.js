// Area chart in the style of Health and Stocks: scrub by touch, mouse or arrow keys.
import { html, useLayoutEffect, useRef, useState, useId } from '../lib.js';
import { linear, niceTicks, monotonePath, areaPath, nearestIndex, bucketMean } from '../layout/scale.js';
import { clockTime, rate } from '../format.js';

const PAD = { top: 12, right: 40, bottom: 22, left: 2 };
const WINDOW = 1800;
const SMOOTH = 30; // seconds per plotted point: 10 s rates are too jumpy to read

function useWidth(ref) {
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    setW(el.clientWidth);
    if (typeof ResizeObserver !== 'function') return undefined;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return w;
}

export function summarize(series) {
  if (!series.length) return null;
  const values = series.map((p) => p[1]);
  const peak = Math.max(...values);
  const avg = values.reduce((a, v) => a + v, 0) / values.length;
  return { now: values[values.length - 1], peak, avg };
}

export function AreaChart({ series, tint = 'blue', label, height = 168 }) {
  const box = useRef(null);
  const width = useWidth(box);
  const [scrub, setScrub] = useState(-1);
  const descId = useId();
  const pts = series || [];
  const stats = summarize(pts);

  if (pts.length < 2) {
    return html`<div class="chart empty-chart" ref=${box}><p>Not enough data yet. The chart fills in as samples arrive.</p></div>`;
  }

  const t1 = pts[pts.length - 1][0];
  const t0 = t1 - WINDOW;
  const shown = bucketMean(pts.filter((p) => p[0] >= t0), SMOOTH);
  const ticks = niceTicks(Math.max(...shown.map((p) => p[1])));
  const x = linear([t0, t1], [PAD.left, Math.max(PAD.left + 1, width - PAD.right)]);
  const y = linear([0, ticks[ticks.length - 1]], [height - PAD.bottom, PAD.top]);
  const xy = shown.map(([t, v]) => [x(t), y(v)]);
  const base = height - PAD.bottom;
  const gradId = `${descId}-g`;
  const active = scrub >= 0 && scrub < shown.length ? shown[scrub] : null;

  const pick = (clientX) => {
    const rect = box.current.getBoundingClientRect();
    const i = nearestIndex(xy, clientX - rect.left);
    setScrub(i);
  };
  const onKey = (e) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      const start = scrub < 0 ? shown.length - 1 : scrub;
      setScrub(Math.max(0, Math.min(shown.length - 1, start + (e.key === 'ArrowRight' ? 1 : -1))));
    } else if (e.key === 'Home') {
      e.preventDefault();
      setScrub(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      setScrub(shown.length - 1);
    } else if (e.key === 'Escape') {
      setScrub(-1);
    }
  };

  const summary = stats
    ? `${label} over the last 30 minutes: now ${rate(stats.now)}, peak ${rate(stats.peak)}, average ${rate(stats.avg)}.`
    : '';

  return html`<div class=${`chart tint-${tint}`} ref=${box} tabindex="0" role="group"
      aria-label=${`${label} chart. Use left and right arrow keys to read values.`} aria-describedby=${descId}
      onPointerDown=${(e) => pick(e.clientX)} onPointerMove=${(e) => (e.pointerType !== 'touch' || e.buttons) && pick(e.clientX)}
      onPointerLeave=${() => setScrub(-1)} onKeyDown=${onKey} onBlur=${() => setScrub(-1)}>
    <p class="sr-only" id=${descId}>${summary}</p>
    ${width > 0 && html`<svg width=${width} height=${height} aria-hidden="true" focusable="false">
      <defs><linearGradient id=${gradId} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" class="grad-top" /><stop offset="1" class="grad-bottom" />
      </linearGradient></defs>
      ${ticks.map((v) => html`<g class="grid">
        <line x1=${PAD.left} x2=${width - PAD.right + 4} y1=${y(v)} y2=${y(v)} />
        <text x=${width - PAD.right + 8} y=${y(v) + 4}>${v >= 1000 ? `${v / 1000}K` : v}</text></g>`)}
      <path class="area" d=${areaPath(xy, base)} fill=${`url(#${gradId})`} />
      <path class="line" d=${monotonePath(xy)} />
      <g class="xlabels">
        <text x=${PAD.left} y=${height - 6}>30m ago</text>
        <text x=${(PAD.left + width - PAD.right) / 2} y=${height - 6} text-anchor="middle">15m</text>
        <text x=${width - PAD.right} y=${height - 6} text-anchor="end">Now</text>
      </g>
      ${active && html`<g class="scrub">
        <line x1=${x(active[0])} x2=${x(active[0])} y1=${PAD.top - 6} y2=${base} />
        <circle cx=${x(active[0])} cy=${y(active[1])} r="5" />
      </g>`}
    </svg>`}
    ${active && html`<div class="scrub-tip" style=${`left:${Math.min(Math.max(x(active[0]), 60), width - 60)}px`}>
      <strong class="num">${rate(active[1])}</strong><span>${clockTime(active[0])}</span></div>`}
    <div class="sr-only" aria-live="polite">${active ? `${rate(active[1])} at ${clockTime(active[0])}` : ''}</div>
  </div>`;
}
