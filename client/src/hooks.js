import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchCache } from './fetchCache.js';

function initialState(key) {
  if (!key) return { key, data: null, error: null, loading: false };
  const hit = fetchCache.getCached(key);
  if (hit !== undefined) return { key, data: hit, error: null, loading: false };
  return { key, data: null, error: null, loading: true };
}

export function useFetch(key, fetcher) {
  const [state, setState] = useState(() => initialState(key));
  const fetcherRef = useRef({ key, fetcher });
  fetcherRef.current = { key, fetcher };
  const request = useRef(0);
  const mounted = useRef(false);
  const load = useCallback((bypass) => {
    // A long-running action may retain the reload callback for an old project.
    if (!mounted.current || fetcherRef.current.key !== key) return;
    const id = ++request.current;
    if (!key) {
      setState({ key, data: null, error: null, loading: false });
      return;
    }
    setState((s) =>
      s.key === key
        ? { ...s, loading: true, error: null }
        : { key, data: null, error: null, loading: true },
    );
    fetchCache.fetchShared(key, fetcherRef.current.fetcher, { bypass }).then(
      (data) => {
        if (id === request.current)
          setState((s) => (s.key === key ? { key, data, error: null, loading: false } : s));
      },
      (error) => {
        if (id === request.current)
          setState((s) => (s.key === key ? { ...s, key, error, loading: false } : s));
      },
    );
  }, [key]);
  const reload = useCallback(() => load(true), [load]);
  useEffect(() => {
    mounted.current = true;
    const cached = initialState(key);
    if (cached.loading) load(false);
    else {
      request.current += 1;
      setState(cached);
    }
    return () => {
      // Ignore completions from a previous key, unmount, or older reload.
      mounted.current = false;
      request.current += 1;
    };
  }, [key, load]);
  // Cached tab switches can render immediately, before the effect runs.
  const current = state.key === key ? state : initialState(key);
  return {
    data: current.data,
    error: current.error,
    loading: current.loading,
    reload,
  };
}
