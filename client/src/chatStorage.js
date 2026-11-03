// Bounds what chat persistence writes to localStorage so history growth can
// never wedge the quota (a failed write would silently strand new chats in
// memory). Newest wins at every level; in-memory state is never trimmed.

export const MAX_STORED_CHATS = 50;
export const MAX_MESSAGES_PER_CHAT = 200;
// ~4MB of UTF-16 code units: headroom below the typical 5MB quota.
export const MAX_STORED_BYTES = 4_000_000;

export function trimChatsForPersist(
  chats,
  { maxChats = MAX_STORED_CHATS, maxMessages = MAX_MESSAGES_PER_CHAT, maxBytes = MAX_STORED_BYTES } = {},
) {
  // Chats are newest-first; messages are oldest-first.
  let kept = (chats ?? []).slice(0, maxChats).map((c) => ({
    ...c,
    messages: (c.messages ?? []).slice(-maxMessages),
  }));
  while (kept.length > 1 && serializedSize(kept) > maxBytes) kept = kept.slice(0, -1);
  return kept;
}

function serializedSize(value) {
  return JSON.stringify(value).length * 2;
}
