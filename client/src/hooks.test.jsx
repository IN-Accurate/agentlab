import { afterEach, describe, expect, it, vi } from 'vitest';
import { createElement, StrictMode } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { useFetch } from './hooks.js';
import { fetchCache } from './fetchCache.js';
import { api } from './api.js';
import { useRail } from './dashboard/RunsTab.jsx';

vi.mock('./api.js', () => ({ api: vi.fn(), proposeFix: vi.fn() }));

let keyId = 0;
const nextKey = () => `hook-test-${keyId++}`;
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('useFetch integration', () => {
  it('survives StrictMode effect replay without duplicate requests or loading forever', async () => {
    const key = nextKey();
    const response = deferred();
    const fetcher = vi.fn(() => response.promise);
    const { result, unmount } = renderHook(() => useFetch(key, fetcher), {
      wrapper: ({ children }) => createElement(StrictMode, null, children),
    });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    await act(async () => response.resolve('ready'));
    expect(result.current.data).toBe('ready');
    expect(result.current.loading).toBe(false);
    const reload = result.current.reload;
    unmount();
    act(() => reload());
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('shares the request between simultaneous consumers', async () => {
    const key = nextKey();
    const response = deferred();
    const fetcher = vi.fn(() => response.promise);
    const { result } = renderHook(() => [useFetch(key, fetcher), useFetch(key, fetcher)]);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(result.current.every((resource) => resource.loading)).toBe(true);
    await act(async () => response.resolve({ value: 'shared' }));
    expect(result.current.map((resource) => resource.data)).toEqual([
      { value: 'shared' }, { value: 'shared' },
    ]);
    expect(result.current.every((resource) => !resource.loading)).toBe(true);
  });

  it('ignores a slow old-project response after switching projects', async () => {
    const a = deferred();
    const b = deferred();
    const aKey = nextKey();
    const bKey = nextKey();
    const aFetch = vi.fn(() => a.promise);
    const bFetch = vi.fn(() => b.promise);
    const { result, rerender } = renderHook(
      ({ key, fetcher }) => useFetch(key, fetcher),
      { initialProps: { key: aKey, fetcher: aFetch } },
    );
    await waitFor(() => expect(aFetch).toHaveBeenCalledTimes(1));
    rerender({ key: bKey, fetcher: bFetch });
    await waitFor(() => expect(bFetch).toHaveBeenCalledTimes(1));
    await act(async () => b.resolve('project B'));
    expect(result.current.data).toBe('project B');
    await act(async () => a.resolve('project A'));
    expect(result.current.data).toBe('project B');
    expect(result.current.loading).toBe(false);
  });

  it('renders a cached destination immediately and protects it from a late source request', async () => {
    const key = nextKey();
    await fetchCache.fetchShared(key, () => 'cached destination');
    const source = deferred();
    const destinationFetch = vi.fn(() => 'unexpected');
    const { result, rerender } = renderHook(
      ({ key, fetcher }) => useFetch(key, fetcher),
      { initialProps: { key: nextKey(), fetcher: () => source.promise } },
    );
    rerender({ key, fetcher: destinationFetch });
    expect(result.current.data).toBe('cached destination');
    expect(result.current.loading).toBe(false);
    await act(async () => source.resolve('late source'));
    expect(result.current.data).toBe('cached destination');
    expect(destinationFetch).not.toHaveBeenCalled();
  });

  it('reloads with the latest same-key fetcher while retaining current data', async () => {
    const key = nextKey();
    const { result, rerender } = renderHook(
      ({ fetcher }) => useFetch(key, fetcher),
      { initialProps: { fetcher: () => Promise.resolve('before') } },
    );
    await waitFor(() => expect(result.current.data).toBe('before'));
    const refreshed = deferred();
    const latestFetch = vi.fn(() => refreshed.promise);
    rerender({ fetcher: latestFetch });
    act(() => result.current.reload());
    expect(result.current.data).toBe('before');
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(latestFetch).toHaveBeenCalledTimes(1));
    await act(async () => refreshed.resolve('after'));
    expect(result.current.data).toBe('after');
    expect(result.current.loading).toBe(false);
  });

  it('does not allow a retained old-project reload to fetch under the old key', async () => {
    const aKey = nextKey();
    const bKey = nextKey();
    const bFetch = vi.fn(() => Promise.resolve('B'));
    const { result, rerender } = renderHook(
      ({ key, fetcher }) => useFetch(key, fetcher),
      { initialProps: { key: aKey, fetcher: () => Promise.resolve('A') } },
    );
    await waitFor(() => expect(result.current.data).toBe('A'));
    const oldReload = result.current.reload;
    rerender({ key: bKey, fetcher: bFetch });
    await waitFor(() => expect(result.current.data).toBe('B'));
    act(() => oldReload());
    expect(bFetch).toHaveBeenCalledTimes(1);
    expect(fetchCache.getCached(aKey)).toBe('A');
    expect(result.current.data).toBe('B');
  });
});

describe('run history pagination', () => {
  it('keeps the loaded page and navigation history when equivalent versions are recreated', async () => {
    const project = nextKey();
    const versions = [{ version_id: `${project}-v1` }, { version_id: `${project}-v2` }];
    const first = { id: `${project}-first`, app_version_id: versions[0].version_id };
    const second = { id: `${project}-second`, app_version_id: versions[1].version_id };
    api.mockImplementation((path) => {
      const params = new URLSearchParams(path.split('?')[1]);
      return Promise.resolve(params.get('cursor')
        ? { items: [second], next_cursor: 'page-3' }
        : { items: [first], next_cursor: 'page-2' });
    });
    const { result, rerender } = renderHook(
      ({ versions }) => useRail(project, versions, 'all', 0),
      { initialProps: { versions } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => result.current.more());
    expect(result.current.page).toBe(2);

    rerender({ versions: versions.map((version) => ({ ...version, name: 'Refreshed metadata' })) });

    expect(result.current.page).toBe(2);
    expect(result.current.items).toEqual([second]);
    expect(result.current.cursor).toBe('page-3');
    expect(result.current.allItems).toEqual([first, second]);
    expect(api).toHaveBeenCalledTimes(2);
    act(() => result.current.previous());
    expect(result.current.page).toBe(1);
    expect(result.current.items).toEqual([first]);
    await act(async () => result.current.more());
    expect(result.current.page).toBe(2);
    expect(result.current.items).toEqual([second]);
    expect(api).toHaveBeenCalledTimes(2);
  });

  it('keeps an in-flight page locked and accepts its result after equivalent versions are recreated', async () => {
    const project = nextKey();
    const versions = [{ version_id: `${project}-v` }];
    const first = { id: `${project}-first`, app_version_id: versions[0].version_id };
    const second = { id: `${project}-second`, app_version_id: versions[0].version_id };
    const nextPage = deferred();
    api.mockImplementation((path) => {
      const params = new URLSearchParams(path.split('?')[1]);
      return params.get('cursor')
        ? nextPage.promise
        : Promise.resolve({ items: [first], next_cursor: 'page-2' });
    });
    const { result, rerender } = renderHook(
      ({ versions }) => useRail(project, versions, versions[0].version_id, 0),
      { initialProps: { versions } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => { void result.current.more(); });
    expect(result.current.loading).toBe(true);

    rerender({ versions: versions.map((version) => ({ ...version })) });

    expect(result.current.loading).toBe(true);
    expect(result.current.page).toBe(1);
    expect(result.current.items).toEqual([first]);
    act(() => { result.current.more(); result.current.more(); });
    expect(api).toHaveBeenCalledTimes(2);
    await act(async () => nextPage.resolve({ items: [second], next_cursor: null }));
    expect(result.current.page).toBe(2);
    expect(result.current.items).toEqual([second]);
    expect(result.current.allItems).toEqual([first, second]);
    expect(result.current.cursor).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('ignores an old project page after switching and prevents duplicate page starts', async () => {
    const projectA = nextKey();
    const projectB = nextKey();
    const versionsA = [{ version_id: `${projectA}-v` }];
    const versionsB = [{ version_id: `${projectB}-v` }];
    const page = deferred();
    api.mockImplementation((path) => {
      const params = new URLSearchParams(path.split('?')[1]);
      if (params.get('cursor')) return page.promise;
      const version = params.get('version_id');
      return Promise.resolve({
        items: [{ id: version, app_version_id: version }],
        next_cursor: version === versionsA[0].version_id ? 'page-2' : null,
      });
    });
    const { result, rerender } = renderHook(
      ({ project, versions }) => useRail(project, versions, versions[0].version_id, 0),
      { initialProps: { project: projectA, versions: versionsA } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items[0].id).toBe(versionsA[0].version_id);
    act(() => { result.current.more(); result.current.more(); });
    expect(api).toHaveBeenCalledTimes(2);
    rerender({ project: projectB, versions: versionsB });
    expect(result.current.items).toEqual([]);
    await waitFor(() => expect(result.current.items[0]?.id).toBe(versionsB[0].version_id));
    await act(async () => page.resolve({
      items: [{ id: 'old-project-page', app_version_id: versionsA[0].version_id }],
      next_cursor: 'old-cursor',
    }));
    expect(result.current.items.map((item) => item.id)).toEqual([versionsB[0].version_id]);
    expect(result.current.cursor).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('ignores errors and loading completion from a previous project page', async () => {
    const projectA = nextKey();
    const projectB = nextKey();
    const versionsA = [{ version_id: `${projectA}-v` }];
    const versionsB = [{ version_id: `${projectB}-v` }];
    const oldPage = deferred();
    const newFirst = deferred();
    api.mockImplementation((path) => {
      const params = new URLSearchParams(path.split('?')[1]);
      if (params.get('cursor')) return oldPage.promise;
      if (params.get('version_id') === versionsB[0].version_id) return newFirst.promise;
      return Promise.resolve({ items: [], next_cursor: 'page-2' });
    });
    const { result, rerender } = renderHook(
      ({ project, versions }) => useRail(project, versions, versions[0].version_id, 0),
      { initialProps: { project: projectA, versions: versionsA } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => { result.current.more(); });
    rerender({ project: projectB, versions: versionsB });
    await act(async () => oldPage.reject(new Error('old project failed')));
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBeNull();
    await act(async () => newFirst.resolve({ items: [], next_cursor: null }));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });
});
