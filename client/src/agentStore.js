// Saved-agent store: records persist in localStorage; invariants are enforced
// here, not only in the UI.
// - 0 agents means 0 defaults; 1 or more means exactly 1 default.
// - The default agent can never be deleted.
// - A new agent becomes default only after its connection succeeds (callers
//   add only on success; records without a credential are refused).
// Agent id is the backend project id: one agent per project.

export const AGENTS_KEY = 'agentlab.agents.v1';
export const KEY_TTL_MS = 2 * 60 * 60 * 1000;

function expiryTime(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'string') return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function asRecord(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const id = raw.id ?? raw.projectId ?? null;
  if (typeof id !== 'string' || !id) return null;
  return {
    id,
    name: raw.name ?? '',
    projectId: typeof raw.projectId === 'string' ? raw.projectId : id,
    versionId: raw.versionId ?? null,
    credentialId: raw.credentialId ?? null,
    credentialSource: ['default', 'custom'].includes(raw.credentialSource) ? raw.credentialSource : null,
    credentialInvalid: raw.credentialInvalid === true,
    endpoint: raw.endpoint ?? '',
    model: raw.model ?? '',
    ...(Array.isArray(raw.memberProjectIds) ? { memberProjectIds: [...raw.memberProjectIds] } : {}),
    ownProject: raw.ownProject ?? false,
    isDefault: raw.isDefault === true,
    connectedAt: typeof raw.connectedAt === 'number' ? raw.connectedAt : null,
    expiresAt: expiryTime(raw.expiresAt ?? raw.expires_at),
  };
}

export function sanitizeAgents(raw, { now = Date.now() } = {}) {
  const seen = new Set();
  const out = [];
  for (const item of raw ?? []) {
    const rec = asRecord(item);
    if (!rec || seen.has(rec.id)) continue;
    seen.add(rec.id);
    rec.connectedAt ??= now;
    rec.expiresAt ??= rec.connectedAt + KEY_TTL_MS;
    out.push(rec);
  }
  if (out.length && out.filter((a) => a.isDefault).length !== 1) {
    out.forEach((a, i) => {
      a.isDefault = i === 0;
    });
  }
  return out;
}

export function loadAgentStore(storage, { now = Date.now() } = {}) {
  try {
    const raw = storage.getItem(AGENTS_KEY);
    if (!raw) {
      const legacy = storage.getItem('agentlab.chats.v1') ?? storage.getItem('testlab.chats.v1');
      return legacy ? sanitizeAgents(JSON.parse(legacy).map((chat) => chat.connection), { now }) : [];
    }
    return sanitizeAgents(JSON.parse(raw), { now });
  } catch {
    return [];
  }
}

export function saveAgentStore(storage, agents) {
  try {
    storage.setItem(AGENTS_KEY, JSON.stringify(agents ?? []));
  } catch {
    // Private browsing / quota: connections last for this tab only.
  }
}

export function isConnected(agent, now = Date.now()) {
  return !!agent?.credentialId && !agent.credentialInvalid && typeof agent.expiresAt === 'number' && now < agent.expiresAt;
}

// Credential IDs are part of the guard: a late failure from an old request must
// never disconnect a replacement key that has already reconnected successfully.
export function invalidateCredential(agents, credentialId) {
  if (!credentialId) return agents;
  let changed = false;
  const next = (agents ?? []).map((agent) => {
    if (agent.credentialId !== credentialId || (agent.credentialInvalid && agent.expiresAt === 0)) return agent;
    changed = true;
    return { ...agent, credentialInvalid: true, expiresAt: 0 };
  });
  return changed ? next : agents;
}

export function reconcileCredentialMetadata(agents, credentialId, metadata) {
  const expiresAt = expiryTime(metadata?.expires_at ?? metadata?.expiresAt);
  if (expiresAt === null || (metadata?.credential_id && metadata.credential_id !== credentialId)) return agents;
  let changed = false;
  const next = (agents ?? []).map((agent) => {
    // An earlier successful metadata response must not undo a later explicit
    // invalidation. A successful reconnect is what clears that marker.
    if (agent.credentialId !== credentialId || agent.credentialInvalid || agent.expiresAt === expiresAt) return agent;
    changed = true;
    return { ...agent, expiresAt };
  });
  return changed ? next : agents;
}

export function getDefaultAgent(agents) {
  return (agents ?? []).find((a) => a.isDefault) ?? null;
}

export function listAgents(agents) {
  return [...(agents ?? [])];
}

export function getAgentForChat(agents, chat) {
  if (chat?.connectionDismissed && !chat.connection) return null;
  return (agents ?? []).find((a) => a.id === chat?.agentId) ?? getDefaultAgent(agents);
}

// Hydrate untouched drafts and refresh credentials for their selected project.
// Historical or explicitly disconnected chats keep their identity and history.
export function restoreChatConnection(chat, agents) {
  if (chat.connectionDismissed && !chat.connection) return chat;
  const agentId = chat.agentId ?? chat.connection?.projectId;
  if (agentId) {
    const current = agents.find((agent) => agent.id === agentId);
    if (!current) return chat;
    return {
      ...chat,
      agentId: current.id,
      connection: { ...chat.connection, ...current, ownProject: chat.connection?.ownProject ?? false },
    };
  }
  const current = getNewChatAgent(agents);
  if (chat.titleSet || chat.messages?.length || !current) return chat;
  return { ...chat, agentId: current.id, connection: { ...current, ownProject: false } };
}

export function getNewChatAgent(agents) {
  const list = agents ?? [];
  return getDefaultAgent(list) ?? list[0] ?? null;
}

export function addAgent(agents, record, { now = Date.now() } = {}) {
  const list = agents ?? [];
  const id = record?.id ?? record?.projectId ?? null;
  if (typeof id !== 'string' || !id || !record?.credentialId) return list;
  const prev = list.find((a) => a.id === id);
  const next = {
    id,
    name: record.name ?? prev?.name ?? '',
    projectId: id,
    versionId: record.versionId ?? prev?.versionId ?? null,
    credentialId: record.credentialId,
    credentialSource: ['default', 'custom'].includes(record.credentialSource)
      ? record.credentialSource
      : prev?.credentialSource ?? null,
    credentialInvalid: false,
    endpoint: record.endpoint ?? prev?.endpoint ?? '',
    model: record.model ?? prev?.model ?? '',
    ...((record.memberProjectIds ?? prev?.memberProjectIds)
      ? { memberProjectIds: [...(record.memberProjectIds ?? prev.memberProjectIds)] }
      : {}),
    ownProject: record.ownProject ?? prev?.ownProject ?? false,
    isDefault: prev ? prev.isDefault : list.length === 0,
    connectedAt: now,
    expiresAt: expiryTime(record.expiresAt ?? record.expires_at) ?? now + KEY_TTL_MS,
  };
  return [next, ...list.filter((a) => a.id !== id)];
}

export function refreshAgentChats(chats, agent) {
  return (chats ?? []).map((chat) => {
    if ((chat.agentId ?? chat.connection?.projectId) !== agent.id) return chat;
    return {
      ...chat,
      agentId: agent.id,
      connectionDismissed: false,
      connection: { ...chat.connection, ...agent, ownProject: chat.connection?.ownProject ?? false },
    };
  });
}

export function setDefault(agents, agentId, { now = Date.now() } = {}) {
  const list = agents ?? [];
  const target = list.find((a) => a.id === agentId);
  if (!target || !isConnected(target, now)) return list;
  return list.map((a) => ({ ...a, isDefault: a.id === agentId }));
}

export function removeAgent(agents, agentId) {
  const list = agents ?? [];
  const target = list.find((a) => a.id === agentId);
  if (!target || target.isDefault) return { agents: list, removed: false };
  return { agents: list.filter((a) => a.id !== agentId), removed: true };
}

export const FIRST_DEFAULT_NOTE = 'This agent will be set as your default agent.';
export const DEFAULT_DONE_NOTE = 'Set as your default agent.';
export const replaceDefaultNote = (name) => `This will replace ${name} as your default.`;

export function defaultFormState(agents) {
  const list = agents ?? [];
  const def = getDefaultAgent(list);
  return {
    isFirst: list.length === 0,
    defaultName: def ? def.name || def.model || def.endpoint || 'your current default' : null,
  };
}

export function repointChats(chats, fromAgentId, toAgent) {
  if (!toAgent) return chats ?? [];
  return (chats ?? []).map((c) => {
    if (c?.agentId !== fromAgentId) return c;
    return {
      ...c,
      agentId: toAgent.id,
      connectionDismissed: false,
      connection: {
        name: toAgent.name,
        projectId: toAgent.projectId,
        versionId: toAgent.versionId,
        credentialId: toAgent.credentialId,
        credentialSource: toAgent.credentialSource ?? null,
        endpoint: toAgent.endpoint,
        model: toAgent.model,
        ...(toAgent.memberProjectIds ? { memberProjectIds: toAgent.memberProjectIds } : {}),
        ownProject: false,
      },
    };
  });
}
