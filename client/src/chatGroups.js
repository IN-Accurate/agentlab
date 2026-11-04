// Pure helpers for the sidebar chat list: creation-time backfill,
// date/agent grouping, and the collapsed visible limit.

export const RECENT_LIMIT = 10;
export const DAY_MS = 24 * 60 * 60 * 1000;

// New chats store createdAt; old ones embed it in the id ("c" + Date.now()).
export function chatCreatedAt(chat) {
  if (chat && typeof chat.createdAt === 'number' && Number.isFinite(chat.createdAt)) {
    return chat.createdAt;
  }
  const match = /^c(\d+)$/.exec(chat?.id ?? '');
  const parsed = match ? Number(match[1]) : NaN;
  return Number.isFinite(parsed) ? parsed : 0;
}

export function chatAgentId(chat) {
  return chat?.agentId ?? chat?.connection?.projectId ?? null;
}

export function startOfToday(nowMs = Date.now()) {
  const d = new Date(nowMs);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

export function dateGroupFor(createdAt, nowMs = Date.now()) {
  const start = startOfToday(nowMs);
  if (createdAt >= start) return 'today';
  if (createdAt >= start - 7 * DAY_MS) return 'week';
  return 'older';
}

export const DATE_GROUP_LABELS = {
  today: 'Today',
  week: 'Previous 7 days',
  older: 'Older',
};

export function agentLabel(chat) {
  const c = chat?.connection;
  if (!c) return 'No agent';
  return c.name || c.model || c.endpoint || 'Agent';
}

export function agentKey(chat) {
  return chat?.connection?.projectId ?? 'none';
}

// Groups in fixed date order (Today, Previous 7 days, Older) or in order
// of first appearance for agents. Chats keep their relative order.
export function groupChats(chats, mode = 'date', nowMs = Date.now()) {
  const groups = [];
  const byKey = new Map();
  for (const chat of chats) {
    const key = mode === 'agent' ? agentKey(chat) : dateGroupFor(chatCreatedAt(chat), nowMs);
    const label = mode === 'agent' ? agentLabel(chat) : DATE_GROUP_LABELS[key];
    let group = byKey.get(key);
    if (!group) {
      group = { key, label, chats: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    group.chats.push(chat);
  }
  if (mode === 'date') {
    const order = { today: 0, week: 1, older: 2 };
    groups.sort((a, b) => order[a.key] - order[b.key]);
  }
  return groups;
}

export function visibleChats(chats, expanded) {
  return expanded ? chats : chats.slice(0, RECENT_LIMIT);
}
