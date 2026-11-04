import { useState } from 'react';
import { api } from '../api.js';
import { useFetch } from '../hooks.js';

export const REQUESTS_PER_PAGE = 4;

export function useTraceRequests(projectId, version, status) {
  const key = JSON.stringify([projectId, version, status]);
  const [paging, setPaging] = useState({ key, cursors: [null], index: 0 });
  // Changing a filter/project starts at page one immediately, including while
  // an earlier page is still loading. Cached previous pages remain reusable.
  const current = paging.key === key ? paging : { key, cursors: [null], index: 0 };
  const cursor = current.cursors[current.index];
  const list = useFetch(projectId ? `trace-requests-${key}-${cursor ?? 'first'}` : null, () => {
    const params = new URLSearchParams({ project_id: projectId, limit: String(REQUESTS_PER_PAGE) });
    if (version !== 'all') params.set('version_id', version);
    if (status !== 'all') params.set('status', status);
    if (cursor) params.set('cursor', cursor);
    return api(`/v1/traces?${params}`);
  });

  function next() {
    if (list.loading || !list.data?.next_cursor) return;
    const index = current.index + 1;
    setPaging({ key, index, cursors: [...current.cursors.slice(0, index), list.data.next_cursor] });
  }

  function previous() {
    if (!current.index || list.loading) return;
    setPaging({ ...current, index: current.index - 1 });
  }

  return {
    ...list,
    items: list.data?.items ?? [],
    page: current.index + 1,
    hasNext: Boolean(list.data?.next_cursor),
    hasPrevious: current.index > 0,
    next,
    previous,
  };
}
