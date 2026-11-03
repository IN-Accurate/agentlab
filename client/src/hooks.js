import { useCallback, useEffect, useState } from 'react';
import { fetchCache } from './fetchCache.js';

function initialState(key) {
  if (!key) return { key, data: null, error: null, loading: false };
  const hit = fetchCache.getCached(key);
  if (hit !== undefined) return { key, data: hit, error: null, loading: false };
  return { key, data: null, error: null, loading: true };
}

export function useFetch(key, fetcher) {
  const [state, setState] = useState(() => initialState(key));
  const reload = useCallback(() => {
    if (!key) {
      setState({ key, data: null, error: null, loading: false });
      return;
    }
    let cancelled = false;
    setState((s) =>
      s.key === key
        ? { ...s, loading: true, error: null }
        : { key, data: null, error: null, loading: true },
    );
    fetchCache.fetchShared(key, fetcher, { bypass: true }).then(
      (data) => {
        if (!cancelled)
          setState((s) => (s.key === key ? { key, data, error: null, loading: false } : s));
      },
      (error) => {
        if (!cancelled)
          setState((s) => (s.key === key ? { ...s, key, error, loading: false } : s));
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  useEffect(() => {
    if (!key) {
      setState({ key, data: null, error: null, loading: false });
      return;
    }
    const hit = fetchCache.getCached(key);
    if (hit !== undefined) {
      setState({ key, data: hit, error: null, loading: false });
      return;
    }
    reload();
  }, [key, reload]);
  const current = state.key === key;
  return {
    data: current ? state.data : null,
    error: current ? state.error : null,
    loading: key ? state.loading || !current : false,
    reload,
  };
}
