// Short-TTL response cache for dashboard fetches: remounts (e.g. tab
// switches) reuse fresh data instead of refetching. Errors are never cached.
// Concurrent callers for one key share a single in-flight request.

const DEFAULT_TTL_MS = 30_000;
const MAX_ENTRIES = 200;

export function createFetchCache(ttlMs = DEFAULT_TTL_MS) {
  const cache = new Map();
  const inflight = new Map();

  function getCached(key) {
    const hit = cache.get(key);
    if (!hit) return undefined;
    if (Date.now() - hit.fetchedAt > ttlMs) {
      cache.delete(key);
      return undefined;
    }
    return hit.data;
  }

  function fetchShared(key, fetcher, { bypass = false } = {}) {
    if (!bypass) {
      const hit = getCached(key);
      if (hit !== undefined) return Promise.resolve(hit);
    }
    const running = inflight.get(key);
    if (running) return running;
    const promise = fetcher().then(
      (data) => {
        inflight.delete(key);
        if (cache.size >= MAX_ENTRIES) cache.delete(cache.keys().next().value);
        cache.set(key, { data, fetchedAt: Date.now() });
        return data;
      },
      (error) => {
        inflight.delete(key);
        throw error;
      },
    );
    inflight.set(key, promise);
    return promise;
  }

  return { getCached, fetchShared };
}

export const fetchCache = createFetchCache();
