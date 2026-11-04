// Pure helpers for the dedicated agent-connection list: dedupe stored or
// backfilled connections and upsert newly connected agents (newest first).

export function dedupeConnections(connections) {
  const seen = new Set();
  const out = [];
  for (const conn of connections ?? []) {
    if (!conn || seen.has(conn.projectId)) continue;
    seen.add(conn.projectId);
    out.push(conn);
  }
  return out;
}

export function upsertAgent(agents, connection) {
  return [connection, ...(agents ?? []).filter((a) => a?.projectId !== connection.projectId)];
}

// A saved agent and a draft chat are restored together. Conversations with
// history keep their original agent; an explicit disconnect also stays put.
export function restoreChatConnection(chat, agents) {
  if (chat.connection) {
    const current = agents.find((agent) => agent.projectId === chat.connection.projectId);
    if (!current) return chat;
    return {
      ...chat,
      connection: { ...chat.connection, ...current, ownProject: chat.connection.ownProject },
    };
  }
  if (chat.connectionDismissed || chat.titleSet || chat.messages?.length || !agents[0]) return chat;
  return { ...chat, connection: { ...agents[0], ownProject: false } };
}
