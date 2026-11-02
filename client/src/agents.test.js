import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { dedupeConnections, upsertAgent } from './agents.js';

describe('dedupeConnections', () => {
  it('drops nulls and keeps the first connection per project', () => {
    const a1 = { projectId: 'p1', credentialId: 'new' };
    const b = { projectId: 'p2', credentialId: 'k' };
    const a2 = { projectId: 'p1', credentialId: 'old' };
    assert.deepEqual(dedupeConnections([null, a1, b, a2, undefined]), [a1, b]);
  });

  it('handles missing input', () => {
    assert.deepEqual(dedupeConnections(undefined), []);
  });
});

describe('upsertAgent', () => {
  it('prepends a new agent', () => {
    const a = { projectId: 'p1' };
    assert.deepEqual(upsertAgent([{ projectId: 'p2' }], a), [a, { projectId: 'p2' }]);
  });

  it('replaces the same project and moves it to the front', () => {
    const old = { projectId: 'p1', credentialId: 'old' };
    const fresh = { projectId: 'p1', credentialId: 'new' };
    assert.deepEqual(upsertAgent([old, { projectId: 'p2' }], fresh), [fresh, { projectId: 'p2' }]);
  });
});
