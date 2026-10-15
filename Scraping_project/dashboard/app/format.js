// Number, time and URL formatting. Pure: no DOM. Every missing value renders as an em dash.
const DASH = '—';
const nf = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

function trim(n, digits = 1) {
  return n.toFixed(digits).replace(/\.0+$/, '');
}

export function compact(n) {
  if (n == null || Number.isNaN(n)) return DASH;
  const a = Math.abs(n);
  if (a < 1000) return nf.format(n);
  for (const [size, unit] of [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']]) {
    if (a >= size * 0.99995) {
      const v = n / size;
      return (Math.abs(v) >= 99.95 ? nf.format(v) : trim(v)) + unit;
    }
  }
  return nf.format(n);
}

export const full = (n) => (n == null || Number.isNaN(n) ? DASH : nf.format(n));

export function rate(perMin) {
  if (perMin == null || Number.isNaN(perMin)) return DASH;
  if (perMin === 0) return '0/min';
  if (perMin < 10) return `${trim(perMin)}/min`;
  return `${compact(Math.round(perMin))}/min`;
}

export function relativeTime(t, now = Date.now() / 1000) {
  if (t == null) return DASH;
  const age = now - t;
  if (age < 5) return 'just now';
  if (age < 60) return `${Math.floor(age)}s ago`;
  if (age < 3600) return `${Math.floor(age / 60)}m ago`;
  if (age < 86400) return `${Math.floor(age / 3600)}h ago`;
  return `${Math.floor(age / 86400)}d ago`;
}

const timeFmt = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' });
export const clockTime = (t) => (t == null ? DASH : timeFmt.format(new Date(t * 1000)));

export function isHttpUrl(url) {
  if (typeof url !== 'string' || !url) return false;
  try {
    const u = new URL(url);
    return (u.protocol === 'http:' || u.protocol === 'https:') && Boolean(u.hostname);
  } catch {
    return false;
  }
}

export function splitUrl(url) {
  if (!isHttpUrl(url)) return { host: null, path: String(url ?? '') };
  const u = new URL(url);
  return { host: u.hostname.toLowerCase(), path: (u.pathname || '/') + u.search };
}

export function bytes(n) {
  if (n == null || Number.isNaN(n)) return DASH;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (Math.abs(v) >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 ? nf.format(v) : trim(v)} ${units[i]}`;
}

export const percent = (x) => (x == null || Number.isNaN(x) ? DASH : `${Math.round(x)}%`);

// Compression ratio (summary size / original size) as "N× smaller".
export function ratio(x) {
  if (x == null || Number.isNaN(x) || x <= 0) return DASH;
  if (x >= 1) return `${trim(x, 2)}×`;
  return `${nf.format(Math.round(1 / x))}× smaller`;
}

const FRESHNESS = { active: 'Active', idle: 'Idle', stale: 'Stale', none: 'No data' };
export const freshnessLabel = (f) => FRESHNESS[f] || DASH;

export const plural = (n, one, many = `${one}s`) => `${nf.format(n)} ${n === 1 ? one : many}`;

export function formatValue(value, fmt) {
  if (value == null) return DASH;
  switch (fmt) {
    case 'int': return full(Math.round(value));
    case 'float': return trim(value, 2);
    case 'bytes': return bytes(value);
    case 'percent': return percent(value);
    case 'ratio': return ratio(value);
    case 'bool': return value ? 'Yes' : 'No';
    case 'time': return clockTime(value);
    default: return String(value);
  }
}
