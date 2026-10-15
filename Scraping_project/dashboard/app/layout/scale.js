// Chart scales and paths. Pure.

export function linear([d0, d1], [r0, r1]) {
  if (d1 === d0) return () => (r0 + r1) / 2;
  const k = (r1 - r0) / (d1 - d0);
  return (v) => r0 + (v - d0) * k;
}

// [0, step, 2*step] with a round step whose top tick is >= max.
export function niceTicks(max, count = 2) {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s * count >= max) ?? 10 * mag;
  return Array.from({ length: count + 1 }, (_, i) => +(i * step).toPrecision(12));
}

const r = (n) => +n.toFixed(2);

// Monotone cubic interpolation (Fritsch-Carlson): no overshoot between points.
export function monotonePath(points) {
  const n = points.length;
  if (!n) return '';
  if (n === 1) return `M${r(points[0][0])},${r(points[0][1])}`;
  const dx = [];
  const slope = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(points[i + 1][0] - points[i][0]);
    slope.push(dx[i] ? (points[i + 1][1] - points[i][1]) / dx[i] : 0);
  }
  const m = [slope[0]];
  for (let i = 1; i < n - 1; i++) {
    m.push(slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2);
  }
  m.push(slope[n - 2]);
  for (let i = 0; i < n - 1; i++) {
    if (slope[i] === 0) {
      m[i] = 0;
      m[i + 1] = 0;
      continue;
    }
    const a = m[i] / slope[i];
    const b = m[i + 1] / slope[i];
    const s = a * a + b * b;
    if (s > 9) {
      const t = 3 / Math.sqrt(s);
      m[i] = t * a * slope[i];
      m[i + 1] = t * b * slope[i];
    }
  }
  let d = `M${r(points[0][0])},${r(points[0][1])}`;
  for (let i = 0; i < n - 1; i++) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[i + 1];
    const h = dx[i] / 3;
    d += `C${r(x0 + h)},${r(y0 + m[i] * h)} ${r(x1 - h)},${r(y1 - m[i + 1] * h)} ${r(x1)},${r(y1)}`;
  }
  return d;
}

export function areaPath(points, baseline) {
  if (!points.length) return '';
  const first = points[0];
  const last = points[points.length - 1];
  return `${monotonePath(points)}L${r(last[0])},${r(baseline)}L${r(first[0])},${r(baseline)}Z`;
}

// Index of the point whose x is closest to `x` (-1 for no points).
export function nearestIndex(points, x) {
  if (!points.length) return -1;
  let lo = 0;
  let hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid][0] <= x) lo = mid;
    else hi = mid;
  }
  return Math.abs(points[lo][0] - x) <= Math.abs(points[hi][0] - x) ? lo : hi;
}

// Mean of [t, value] points per `size`-second bucket, keyed by bucket start.
export function bucketMean(points, size) {
  if (!(size > 0)) return points;
  const out = [];
  for (const [t, v] of points) {
    const key = Math.floor(t / size) * size;
    const last = out[out.length - 1];
    if (last && last[0] === key) {
      last[2] += v;
      last[3] += 1;
    } else {
      out.push([key, 0, v, 1]);
    }
  }
  return out.map(([key, , sum, n]) => [key, sum / n]);
}
