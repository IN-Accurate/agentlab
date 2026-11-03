import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { trimChatsForPersist } from './chatStorage.js';

const mkChat = (id, nMessages) => ({
  id,
  messages: Array.from({ length: nMessages }, (_, i) => ({ id: `${id}-m${i}` })),
});

describe('trimChatsForPersist', () => {
  it('passes small histories through untouched', () => {
    const chats = [mkChat('c1', 3), mkChat('c0', 1)];
    assert.deepEqual(trimChatsForPersist(chats), chats);
  });

  it('keeps the newest chats and newest messages', () => {
    const chats = [mkChat('new', 5), mkChat('mid', 5), mkChat('old', 5)];
    const out = trimChatsForPersist(chats, { maxChats: 2, maxMessages: 2, maxBytes: 1e9 });
    assert.deepEqual(
      out.map((c) => c.id),
      ['new', 'mid'],
    );
    assert.deepEqual(
      out[0].messages.map((m) => m.id),
      ['new-m3', 'new-m4'],
    );
  });

  it('drops oldest chats first when over the byte budget, keeping one', () => {
    const chats = [mkChat('new', 2), mkChat('old', 2)];
    const one = JSON.stringify([chats[0]]).length * 2;
    const out = trimChatsForPersist(chats, { maxBytes: one });
    assert.deepEqual(
      out.map((c) => c.id),
      ['new'],
    );
  });

  it('handles missing input', () => {
    assert.deepEqual(trimChatsForPersist(undefined), []);
    assert.deepEqual(trimChatsForPersist([{ id: 'c' }]), [{ id: 'c', messages: [] }]);
  });
});
