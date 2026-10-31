import { useCallback, useEffect, useState } from 'react';

export function useFetch(key, fetcher) {
  const [state, setState] = useState({ data: null, error: null, loading: !!key });
  const reload = useCallback(() => {
    if (!key) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fetcher().then(
      (data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      },
      (error) => {
        if (!cancelled)
          setState((s) => ({ ...s, error, loading: false }));
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  useEffect(() => reload(), [reload]);
  return { ...state, reload };
}
