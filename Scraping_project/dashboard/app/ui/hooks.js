import { useEffect, useState, useCallback, useRef } from '../lib.js';
import { getJSON } from '../api.js';

// Fetch JSON for a view. `refreshKey` changing refetches in place (keeps old data while loading).
export function useApi(path, refreshKey) {
  const [res, setRes] = useState({ data: null, error: null, loading: true });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!path) return undefined;
    const ctrl = new AbortController();
    setRes((r) => ({ ...r, loading: true }));
    getJSON(path, { signal: ctrl.signal })
      .then((data) => setRes({ data, error: null, loading: false }))
      .catch((error) => {
        if (error.name !== 'AbortError') setRes((r) => ({ data: r.data, error, loading: false }));
      });
    return () => ctrl.abort();
  }, [path, refreshKey, attempt]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...res, retry };
}

export function useMedia(query) {
  const get = () => (typeof matchMedia === 'function' ? matchMedia(query).matches : false);
  const [match, setMatch] = useState(get);
  useEffect(() => {
    if (typeof matchMedia !== 'function') return undefined;
    const m = matchMedia(query);
    const on = () => setMatch(m.matches);
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, [query]);
  return match;
}

export const useReducedMotion = () => useMedia('(prefers-reduced-motion: reduce)');

// `value`, but updated at most once per `ms` (for refetching on live version bumps).
export function useThrottled(value, ms) {
  const [current, setCurrent] = useState(value);
  const last = useRef(0);
  useEffect(() => {
    if (value === current) return undefined;
    const wait = Math.max(0, last.current + ms - Date.now());
    const timer = setTimeout(() => {
      last.current = Date.now();
      setCurrent(value);
    }, wait);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return current;
}

// Copy text, falling back to a selection copy where the async clipboard needs HTTPS.
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.className = 'sr-only';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  ta.remove();
  return ok;
}
