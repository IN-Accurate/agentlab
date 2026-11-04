// Move a selected draft into the response to its user request. This also handles
// draft cards restored from older clients, which stored them before the user turn.
export function appendTurn(messages, user, assistant, runCard) {
  const previous = runCard
    ? messages.map((message) => ({
        ...message,
        cards: (message.cards ?? []).filter((card) => card.id !== runCard.id),
      })).filter((message) => message.role === 'user' || message.text || message.cards.length)
    : messages;
  return [
    ...previous,
    user,
    { ...assistant, cards: runCard ? [{ ...runCard, status: 'running', progress: 'Running test…' }] : [] },
  ];
}

export function removeDraft(messages, cardId) {
  return messages.map((message) => ({
    ...message,
    cards: (message.cards ?? []).filter((card) => card.id !== cardId || card.status !== 'ready'),
  })).filter((message) => message.role === 'user' || message.text || message.cards.length);
}

export const hasUserMessage = (messages) => messages.some((message) => message.role === 'user');

/**
 * All policy or context given in this chat's own messages (not test runs), oldest
 * first and without repeats, plus the context sent now. It goes with every chat
 * message so the agent keeps answering from it for the whole conversation.
 */
export function chatContext(messages, current = '') {
  const parts = [];
  for (const item of [
    ...(messages ?? []).filter((m) => m.role === 'user' && (m.kind ?? 'chat') === 'chat').map((m) => m.context),
    current,
  ]) {
    const text = typeof item === 'string' ? item.trim() : '';
    if (text && !parts.includes(text)) parts.push(text);
  }
  return parts.join('\n\n');
}
