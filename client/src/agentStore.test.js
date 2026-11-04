import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_DONE_NOTE,
  FIRST_DEFAULT_NOTE,
  KEY_TTL_MS,
  addAgent,
  defaultFormState,
  getAgentForChat,
  getDefaultAgent,
  getNewChatAgent,
  isConnected,
  invalidateCredential,
  listAgents,
  loadAgentStore,
  removeAgent,
  replaceDefaultNote,
  repointChats,
  reconcileCredentialMetadata,
  refreshAgentChats,
  restoreChatConnection,
  sanitizeAgents,
  saveAgentStore,
  setDefault,
} from './agentStore.js';

const T0 = 1_700_000_000_000;

const conn = (over = {}) => ({
  id: 'p1',
  projectId: 'p1',
  credentialId: 'k1',
  name: 'A',
  model: 'm',
  endpoint: 'e',
  ...over,
});

const countDefaults = (agents) => agents.filter((a) => a.isDefault).length;

function fakeStorage(initial = {}) {
  const m = new Map(Object.entries(initial));
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => {
      m.set(k, v);
    },
  };
}

describe('addAgent', () => {
  it('makes the first agent default only after a successful connect', () => {
    const out = addAgent([], conn(), { now: T0 });
    assert.equal(out.length, 1);
    assert.equal(out[0].isDefault, true);
    assert.equal(out[0].expiresAt, T0 + KEY_TTL_MS);
  });

  it('saves nothing for a failed connect', () => {
    const before = [];
    assert.strictEqual(addAgent(before, { id: 'p1' }, { now: T0 }), before);
    assert.strictEqual(addAgent(before, null, { now: T0 }), before);
    assert.deepEqual(before, []);
  });

  it('keeps later agents non-default and preserves flags on refresh', () => {
    let agents = addAgent([], conn(), { now: T0 });
    agents = addAgent(agents, conn({ id: 'p2', projectId: 'p2', credentialId: 'k2' }), { now: T0 });
    assert.deepEqual(
      agents.map((a) => a.isDefault),
      [false, true],
    );
    agents = addAgent(agents, conn({ credentialId: 'k1b' }), { now: T0 + 1 });
    assert.equal(agents[0].credentialId, 'k1b');
    assert.equal(agents[0].isDefault, true);
    assert.equal(countDefaults(agents), 1);
  });
});

describe('setDefault', () => {
  it('swaps the default on and off leaving exactly one', () => {
    let agents = addAgent([], conn(), { now: T0 });
    agents = addAgent(agents, conn({ id: 'p2', projectId: 'p2', credentialId: 'k2' }), { now: T0 });
    agents = setDefault(agents, 'p2', { now: T0 });
    assert.deepEqual(
      agents.map((a) => [a.id, a.isDefault]),
      [
        ['p2', true],
        ['p1', false],
      ],
    );
    agents = setDefault(agents, 'p1', { now: T0 });
    assert.equal(getDefaultAgent(agents).id, 'p1');
    assert.equal(countDefaults(agents), 1);
  });

  it('refuses missing or expired agents', () => {
    const agents = addAgent([], conn(), { now: T0 });
    assert.strictEqual(setDefault(agents, 'nope', { now: T0 }), agents);
    assert.strictEqual(setDefault(agents, 'p1', { now: T0 + KEY_TTL_MS + 1 }), agents);
    assert.equal(getDefaultAgent(agents).id, 'p1');
  });
});

describe('removeAgent', () => {
  it('refuses to delete the default', () => {
    const agents = addAgent([], conn(), { now: T0 });
    const out = removeAgent(agents, 'p1');
    assert.equal(out.removed, false);
    assert.deepEqual(out.agents, agents);
  });

  it('deletes a non-default and re-points its chats to the default', () => {
    let agents = addAgent([], conn(), { now: T0 });
    agents = addAgent(agents, conn({ id: 'p2', projectId: 'p2', credentialId: 'k2' }), { now: T0 });
    const out = removeAgent(agents, 'p2');
    assert.equal(out.removed, true);
    assert.deepEqual(
      out.agents.map((a) => a.id),
      ['p1'],
    );
    const chats = [
      { id: 'c1', agentId: 'p2', connection: { projectId: 'p2' } },
      { id: 'c2', agentId: 'p1', connection: { projectId: 'p1' } },
    ];
    const repointed = repointChats(chats, 'p2', getDefaultAgent(out.agents));
    assert.equal(repointed[0].agentId, 'p1');
    assert.equal(repointed[0].connection.credentialId, 'k1');
    assert.equal(repointed[0].connection.ownProject, false);
    assert.equal(repointed[1].connection.projectId, 'p1');
    assert.equal(repointed[1], chats[1]);
  });
});

describe('isConnected', () => {
  it('flips to false once the key expires without any reload', () => {
    const [agent] = addAgent([], conn(), { now: T0 });
    assert.equal(isConnected(agent, T0), true);
    assert.equal(isConnected(agent, T0 + KEY_TTL_MS - 1), true);
    assert.equal(isConnected(agent, T0 + KEY_TTL_MS + 1), false);
    assert.equal(isConnected({ ...agent, credentialId: null }, T0), false);
  });
});

describe('credential recovery', () => {
  it('invalidates the failed key without changing agent identity or default selection', () => {
    const agents = addAgent([], conn(), { now: T0 });
    const invalid = invalidateCredential(agents, 'k1');
    assert.equal(invalid[0].id, 'p1');
    assert.equal(invalid[0].isDefault, true);
    assert.equal(invalid[0].credentialId, 'k1');
    assert.equal(invalid[0].expiresAt, 0);
    assert.equal(isConnected(invalid[0], T0), false);
    const storage = fakeStorage();
    saveAgentStore(storage, invalid);
    assert.equal(isConnected(loadAgentStore(storage, { now: T0 })[0], T0), false);
  });

  it('ignores failures and metadata for a key that has already been replaced', () => {
    const agents = addAgent([], conn({ credentialId: 'replacement' }), { now: T0 });
    assert.equal(invalidateCredential(agents, 'k1'), agents);
    assert.equal(reconcileCredentialMetadata(agents, 'k1', { expires_at: new Date(T0 - 1).toISOString() }), agents);
    assert.equal(isConnected(agents[0], T0), true);
  });

  it('uses authoritative expiry and does not revive explicitly invalid credentials from a late metadata response', () => {
    const agents = addAgent([], conn(), { now: T0 });
    const reconciled = reconcileCredentialMetadata(agents, 'k1', {
      credential_id: 'k1', expires_at: new Date(T0 - 1).toISOString(),
    });
    assert.equal(isConnected(reconciled[0], T0), false);
    const invalid = invalidateCredential(agents, 'k1');
    assert.equal(reconcileCredentialMetadata(invalid, 'k1', {
      expires_at: new Date(T0 + KEY_TTL_MS).toISOString(),
    }), invalid);
    assert.equal(reconcileCredentialMetadata(agents, 'k1', { expires_at: 'invalid' }), agents);
  });

  it('reconnects with the backend expiry and refreshes matching chats without changing their history', () => {
    const invalid = invalidateCredential(addAgent([], conn(), { now: T0 }), 'k1');
    const expiry = T0 + 60_000;
    const [fresh] = addAgent(invalid, conn({ credentialId: 'replacement', expires_at: new Date(expiry).toISOString(), credentialSource: 'custom' }), { now: T0 + 1 });
    assert.equal(fresh.expiresAt, expiry);
    assert.equal(fresh.credentialInvalid, false);
    assert.equal(fresh.isDefault, true);
    const storage = fakeStorage();
    saveAgentStore(storage, [fresh]);
    assert.equal(loadAgentStore(storage, { now: T0 + 1 })[0].credentialSource, 'custom');
    const messages = [{ id: 'm1', text: 'Original response' }];
    const history = { id: 'chat', agentId: 'p1', connectionDismissed: true, messages, title: 'Original chat' };
    const other = { id: 'other', agentId: 'p2', messages: [] };
    const refreshed = refreshAgentChats([history, other], fresh);
    assert.equal(refreshed.length, 2);
    assert.equal(refreshed[0].id, history.id);
    assert.equal(refreshed[0].title, history.title);
    assert.equal(refreshed[0].messages, messages);
    assert.equal(refreshed[0].connection.credentialId, 'replacement');
    assert.equal(refreshed[0].connection.credentialSource, 'custom');
    assert.equal(refreshed[0].connectionDismissed, false);
    assert.equal(refreshed[1], other);
  });
});

describe('migration and persistence', () => {
  it('backfills flags, expiry, and drops dupes and bad records', () => {
    const legacy = [
      { projectId: 'p2', credentialId: 'k2', model: 'm2' },
      { projectId: 'p1', credentialId: 'k1', model: 'm1' },
      { projectId: 'p2', credentialId: 'stale' },
      { model: 'no-id' },
      null,
    ];
    const out = sanitizeAgents(legacy, { now: T0 });
    assert.deepEqual(
      out.map((a) => [a.id, a.credentialId, a.isDefault]),
      [
        ['p2', 'k2', true],
        ['p1', 'k1', false],
      ],
    );
    assert.equal(out[0].expiresAt, T0 + KEY_TTL_MS);
  });

  it('state survives a reload', () => {
    let agents = addAgent([], conn(), { now: T0 });
    agents = addAgent(agents, conn({ id: 'p2', projectId: 'p2', credentialId: 'k2' }), { now: T0 });
    agents = setDefault(agents, 'p2', { now: T0 });
    const storage = fakeStorage();
    saveAgentStore(storage, agents);
    assert.deepEqual(loadAgentStore(storage, { now: T0 + 1000 }), agents);
    assert.deepEqual(loadAgentStore(fakeStorage()), []);
  });

  it('backfills legacy chat connections only when the saved-agent key is missing', () => {
    const chatData = JSON.stringify([{ connection: conn() }, { connection: conn() }]);
    const migrated = loadAgentStore(fakeStorage({ 'agentlab.chats.v1': chatData }), { now: T0 });
    assert.equal(migrated.length, 1);
    assert.equal(migrated[0].id, 'p1');
    assert.equal(migrated[0].isDefault, true);
    assert.deepEqual(loadAgentStore(fakeStorage({
      'agentlab.chats.v1': chatData,
      'agentlab.agents.v1': '[]',
    }), { now: T0 }), []);
  });

  it('preserves team member project IDs on migration, refresh, and reload', () => {
    const team = conn({ memberProjectIds: ['member-a', 'member-b'] });
    const migrated = sanitizeAgents([team], { now: T0 });
    const refreshed = addAgent(migrated, conn({ credentialId: 'fresh-key' }), { now: T0 + 1 });
    const storage = fakeStorage();
    saveAgentStore(storage, refreshed);
    assert.deepEqual(loadAgentStore(storage, { now: T0 + 2 })[0].memberProjectIds, team.memberProjectIds);
  });
});

describe('restored chat connections', () => {
  const newest = { ...conn({ id: 'p2', projectId: 'p2' }), isDefault: false };
  const defaultAgent = { ...conn({ credentialId: 'fresh-key' }), isDefault: true };
  const agents = [newest, defaultAgent];
  const draft = { id: 'draft', titleSet: false, connection: null, messages: [] };

  it('hydrates a default draft from the designated default instead of the newest agent', () => {
    const restored = restoreChatConnection(draft, agents);
    assert.equal(restored.agentId, 'p1');
    assert.equal(restored.connection.credentialId, 'fresh-key');
    assert.equal(restored.connection.ownProject, false);
    assert.equal(draft.connection, null);
  });

  it('refreshes the selected project credential while preserving ownership and history', () => {
    const history = [{ role: 'user', text: 'Original request' }];
    const chat = { ...draft, agentId: 'p1', connection: { projectId: 'p1', credentialId: 'old', ownProject: true }, messages: history };
    const restored = restoreChatConnection(chat, agents);
    assert.equal(restored.connection.credentialId, 'fresh-key');
    assert.equal(restored.connection.projectId, 'p1');
    assert.equal(restored.connection.ownProject, true);
    assert.equal(restored.messages, history);
    assert.equal(chat.connection.credentialId, 'old');
  });

  it('keeps disconnected history and explicit disconnects untouched during hydration', () => {
    const history = { ...draft, messages: [{ role: 'user', text: 'Previous request' }] };
    const dismissed = { ...draft, agentId: 'p1', connectionDismissed: true };
    assert.equal(restoreChatConnection(history, agents), history);
    assert.equal(restoreChatConnection(dismissed, agents), dismissed);
    assert.equal(getAgentForChat(agents, dismissed), null);
  });

  it('keeps a missing original agent during hydration and leaves default fallback to selection', () => {
    const chat = { ...draft, agentId: 'gone', connection: { projectId: 'gone' } };
    assert.equal(restoreChatConnection(chat, agents), chat);
    assert.equal(getAgentForChat(agents, chat), defaultAgent);
    assert.equal(restoreChatConnection(draft, []), draft);
  });
});

describe('default form state', () => {
  it('detects the first agent', () => {
    assert.deepEqual(defaultFormState([]), { isFirst: true, defaultName: null });
  });

  it('names the current default afterwards', () => {
    const agents = [
      { id: 'p2', isDefault: false, name: '', model: '', endpoint: '' },
      { id: 'p1', isDefault: true, name: '', model: 'm1', endpoint: 'e1' },
    ];
    assert.deepEqual(defaultFormState(agents), { isFirst: false, defaultName: 'm1' });
  });

  it('uses the exact note copy', () => {
    assert.equal(FIRST_DEFAULT_NOTE, 'This agent will be set as your default agent.');
    assert.equal(DEFAULT_DONE_NOTE, 'Set as your default agent.');
    assert.equal(replaceDefaultNote('Acme'), 'This will replace Acme as your default.');
  });

  it('connect with the toggle on moves the default', () => {
    let agents = addAgent([], conn(), { now: T0 });
    agents = addAgent(agents, conn({ id: 'p2', projectId: 'p2', credentialId: 'k2' }), { now: T0 });
    agents = setDefault(agents, 'p2', { now: T0 });
    assert.equal(getDefaultAgent(agents).id, 'p2');
    assert.equal(countDefaults(agents), 1);
  });
});

describe('getNewChatAgent', () => {
  it('inherits the default even after expiry, else the newest, else null', () => {
    assert.equal(getNewChatAgent([]), null);
    let agents = addAgent([], conn(), { now: T0 });
    agents = addAgent(agents, conn({ id: 'p2', projectId: 'p2', credentialId: 'k2' }), { now: T0 });
    assert.equal(getNewChatAgent(agents).id, 'p1');
    const expired = T0 + KEY_TTL_MS + 1;
    assert.equal(getNewChatAgent(agents).id, 'p1');
    assert.ok(!isConnected(getNewChatAgent(agents), expired));
    assert.equal(getNewChatAgent([{ id: 'x' }, { id: 'y' }]).id, 'x');
  });
});

describe('read API', () => {
  it('resolves chats to their agent or the default', () => {
    let agents = addAgent([], conn(), { now: T0 });
    agents = addAgent(agents, conn({ id: 'p2', projectId: 'p2', credentialId: 'k2' }), { now: T0 });
    assert.equal(getAgentForChat(agents, { agentId: 'p2' }).id, 'p2');
    assert.equal(getAgentForChat(agents, { agentId: 'gone' }).id, 'p1');
    assert.equal(getAgentForChat(agents, {}).id, 'p1');
    assert.equal(getAgentForChat([], { agentId: 'p1' }), null);
    assert.deepEqual(
      listAgents(agents).map((a) => a.id),
      ['p2', 'p1'],
    );
  });
});
