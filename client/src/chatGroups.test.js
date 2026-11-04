import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  DAY_MS,
  RECENT_LIMIT,
  agentKey,
  agentLabel,
  chatAgentId,
  chatCreatedAt,
  dateGroupFor,
  groupChats,
  startOfToday,
  visibleChats,
} from './chatGroups.js';

describe('chatCreatedAt', () => {
  it('prefers an explicit createdAt', () => {
    assert.equal(chatCreatedAt({ id: 'c1000', createdAt: 2000 }), 2000);
  });

  it('backfills from the id timestamp', () => {
    assert.equal(chatCreatedAt({ id: 'c1700000000000' }), 1700000000000);
  });

  it('falls back to 0 for unknown ids', () => {
    assert.equal(chatCreatedAt({ id: 'legacy-chat' }), 0);
    assert.equal(chatCreatedAt({}), 0);
  });
});

describe('chatAgentId', () => {
  it('prefers an explicit agentId and backfills from the connection', () => {
    assert.equal(chatAgentId({ agentId: 'p1', connection: { projectId: 'p2' } }), 'p1');
    assert.equal(chatAgentId({ connection: { projectId: 'p2' } }), 'p2');
    assert.equal(chatAgentId({}), null);
  });
});

describe('dateGroupFor', () => {
  const now = new Date(2026, 4, 10, 12, 0, 0).getTime();
  const start = startOfToday(now);

  it('groups today from local midnight', () => {
    assert.equal(dateGroupFor(start, now), 'today');
    assert.equal(dateGroupFor(start - 1, now), 'week');
  });

  it('covers the previous 7 days then rolls to older', () => {
    assert.equal(dateGroupFor(start - 7 * DAY_MS, now), 'week');
    assert.equal(dateGroupFor(start - 7 * DAY_MS - 1, now), 'older');
  });
});

describe('groupChats', () => {
  const now = new Date(2026, 4, 10, 12, 0, 0).getTime();
  const start = startOfToday(now);
  const mk = (id, createdAt, connection = null) => ({ id, createdAt, connection });

  it('orders date groups Today, Previous 7 days, Older regardless of input order', () => {
    const chats = [
      mk('old', start - 30 * DAY_MS),
      mk('new', start + 1000),
      mk('week', start - 2 * DAY_MS),
      mk('new2', start + 2000),
    ];
    const groups = groupChats(chats, 'date', now);
    assert.deepEqual(
      groups.map((g) => g.label),
      ['Today', 'Previous 7 days', 'Older'],
    );
    assert.deepEqual(
      groups[0].chats.map((c) => c.id),
      ['new', 'new2'],
    );
  });

  it('groups by agent in order of first appearance', () => {
    const a = { endpoint: 'https://a.example', model: 'm1', name: 'Refund bot', projectId: 'pa' };
    const b = { endpoint: 'https://b.example', model: 'm2', projectId: 'pb' };
    const chats = [mk('1', 1, a), mk('2', 2), mk('3', 3, b), mk('4', 4, a)];
    const groups = groupChats(chats, 'agent', now);
    assert.deepEqual(
      groups.map((g) => g.label),
      ['Refund bot', 'No agent', 'm2'],
    );
    assert.deepEqual(
      groups[0].chats.map((c) => c.id),
      ['1', '4'],
    );
  });

  it('derives distinct agent keys and labels', () => {
    assert.equal(agentKey({ connection: null }), 'none');
    assert.equal(agentLabel({ connection: null }), 'No agent');
    assert.equal(agentLabel({ connection: { model: 'm' } }), 'm');
    assert.equal(agentLabel({ connection: { endpoint: 'e' } }), 'e');
    assert.equal(agentKey({ connection: { projectId: 'p1' } }), 'p1');
    assert.equal(agentKey({ connection: {} }), 'none');
    assert.notEqual(
      agentKey({ connection: { projectId: 'p1' } }),
      agentKey({ connection: { projectId: 'p2' } }),
    );
  });
});

describe('visibleChats', () => {
  const chats = Array.from({ length: RECENT_LIMIT + 3 }, (_, i) => ({ id: `c${i}` }));

  it('shows the 10 newest until expanded', () => {
    assert.equal(visibleChats(chats, false).length, RECENT_LIMIT);
    assert.equal(visibleChats(chats, true).length, RECENT_LIMIT + 3);
  });
});
