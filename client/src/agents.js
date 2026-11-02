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
