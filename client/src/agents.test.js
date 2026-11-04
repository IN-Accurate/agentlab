import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { dedupeConnections, restoreChatConnection, upsertAgent } from './agents.js';

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

describe('restoreChatConnection', () => {
  const agent = { projectId: 'p1', credentialId: 'latest-key', model: 'muse-spark-1.3' };
  const draft = { id: 'c1', title: 'New chat', titleSet: false, messages: [], connection: null };

  it('connects the restored untouched default draft to the saved agent on its first render', () => {
    const restored = restoreChatConnection(draft, [agent]);
    assert.deepEqual(restored.connection, { ...agent, ownProject: false });
    assert.equal(restored.id, draft.id);
    assert.equal(draft.connection, null);
  });

  it('keeps a disconnected historical conversation and an explicitly disconnected draft unchanged', () => {
    const history = { ...draft, messages: [{ role: 'user', text: 'Previous test' }] };
    const disconnected = { ...draft, connectionDismissed: true };
    const titled = { ...draft, titleSet: true, title: 'Previous test' };
    assert.equal(restoreChatConnection(history, [agent]), history);
    assert.equal(restoreChatConnection(disconnected, [agent]), disconnected);
    assert.equal(restoreChatConnection(titled, [agent]), titled);
  });

  it('preserves a chat’s own agent instead of switching it to the most recent connection', () => {
    const other = { projectId: 'p2', credentialId: 'other-key', ownProject: true };
    const chat = { ...draft, connection: other };
    assert.equal(restoreChatConnection(chat, [agent]), chat);
  });

  it('refreshes a matching project’s saved credential without changing project ownership', () => {
    const chat = {
      ...draft,
      connection: { ...agent, credentialId: 'stale-key', ownProject: true },
    };
    const restored = restoreChatConnection(chat, [{ projectId: 'p2' }, agent]);
    assert.equal(restored.connection.projectId, 'p1');
    assert.equal(restored.connection.credentialId, 'latest-key');
    assert.equal(restored.connection.ownProject, true);
    assert.equal(chat.connection.credentialId, 'stale-key');
  });

  it('leaves the draft disconnected when no saved agent exists', () => {
    assert.equal(restoreChatConnection(draft, []), draft);
  });
});
