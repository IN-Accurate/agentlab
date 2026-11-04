import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createFetchCache } from './fetchCache.js';

const tick = () => new Promise((r) => setTimeout(r, 0));

describe('fetchCache', () => {
  it('serves repeated keys without refetching', async () => {
    const cache = createFetchCache(60_000);
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      return { n: calls };
    };
    assert.deepEqual(await cache.fetchShared('k', fetcher), { n: 1 });
    assert.deepEqual(cache.getCached('k'), { n: 1 });
    assert.deepEqual(await cache.fetchShared('k', fetcher), { n: 1 });
    assert.equal(calls, 1);
  });

  it('refetches after the TTL expires', async () => {
    const cache = createFetchCache(-1);
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      return calls;
    };
    assert.equal(await cache.fetchShared('k', fetcher), 1);
    assert.equal(cache.getCached('k'), undefined);
    assert.equal(await cache.fetchShared('k', fetcher), 2);
  });

  it('shares one in-flight request and never caches errors', async () => {
    const cache = createFetchCache(60_000);
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      await tick();
      return 'ok';
    };
    const [a, b] = await Promise.all([
      cache.fetchShared('k', fetcher),
      cache.fetchShared('k', fetcher),
    ]);
    assert.equal(a, 'ok');
    assert.equal(b, 'ok');
    assert.equal(calls, 1);

    const failing = async () => {
      throw new Error('boom');
    };
    await assert.rejects(() => cache.fetchShared('e', failing), /boom/);
    assert.equal(cache.getCached('e'), undefined);
  });

  it('bypass refetches and refreshes the entry', async () => {
    const cache = createFetchCache(60_000);
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      return calls;
    };
    assert.equal(await cache.fetchShared('k', fetcher), 1);
    assert.equal(await cache.fetchShared('k', fetcher, { bypass: true }), 2);
    assert.equal(cache.getCached('k'), 2);
  });

  it('shares synchronous fetchers and turns synchronous failures into retryable rejections', async () => {
    const cache = createFetchCache(60_000);
    let calls = 0;
    const fetcher = () => ++calls;
    const [a, b] = await Promise.all([
      cache.fetchShared('sync', fetcher),
      cache.fetchShared('sync', fetcher),
    ]);
    assert.equal(a, 1);
    assert.equal(b, 1);
    assert.equal(calls, 1);

    await assert.rejects(cache.fetchShared('error', () => {
      throw new Error('sync failure');
    }), /sync failure/);
    assert.equal(cache.getCached('error'), undefined);
    assert.equal(await cache.fetchShared('error', () => 'recovered'), 'recovered');
  });
});
