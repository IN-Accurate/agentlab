import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { appendTurn, hasUserMessage, removeDraft } from './turns.js';

const card = { id: 'test', kind: 'test', status: 'ready', expected: 'No invented facts' };
const user = { id: 'u', role: 'user', text: 'Run test', cards: [] };
const assistant = { id: 'a', role: 'assistant', text: '', cards: [] };

describe('chat turns', () => {
  it('puts the user request before a selected test and running agent response', () => {
    const result = appendTurn([], user, assistant, card);
    assert.equal(result[0], user);
    assert.equal(result[1].role, 'assistant');
    assert.equal(result[1].cards[0].status, 'running');
    assert.equal(card.status, 'ready');
  });

  it('moves legacy draft cards after the user while preserving previous replies', () => {
    const old = { id: 'old', role: 'assistant', text: '', cards: [card] };
    const reply = { id: 'reply', role: 'assistant', text: 'Earlier response', cards: [] };
    const result = appendTurn([old, reply], user, assistant, card);
    assert.deepEqual(result.map((m) => m.id), ['reply', 'u', 'a']);
    assert.equal(result.flatMap((m) => m.cards).length, 1);
  });

  it('removes draft suggestions without removing results or user turns', () => {
    const resultCard = { ...card, id: 'result', status: 'done' };
    const messages = [user, { ...assistant, cards: [card, resultCard] }];
    const remaining = removeDraft(messages, card.id);
    assert.deepEqual(remaining[1].cards, [resultCard]);
    assert.deepEqual(removeDraft(remaining, resultCard.id), remaining);
  });

  it('hides suggestions after the first user turn, even when the request fails', () => {
    assert.equal(hasUserMessage([assistant]), false);
    assert.equal(hasUserMessage([user, assistant]), true);
  });
});
