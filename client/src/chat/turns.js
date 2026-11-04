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
