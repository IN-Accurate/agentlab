import { useCallback, useEffect, useState } from 'react';

export function useFetch(key, fetcher) {
  const [state, setState] = useState({ key, data: null, error: null, loading: !!key });
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
    fetcher().then(
      (data) => {
        if (!cancelled) setState({ key, data, error: null, loading: false });
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
  useEffect(() => reload(), [reload]);
  const current = state.key === key;
  return {
    data: current ? state.data : null,
    error: current ? state.error : null,
    loading: key ? state.loading || !current : false,
    reload,
  };
}
