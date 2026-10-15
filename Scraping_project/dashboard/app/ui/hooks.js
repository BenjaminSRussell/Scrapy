import { useEffect, useState, useCallback } from '../lib.js';
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
