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
  listAgents,
  loadAgentStore,
  removeAgent,
  replaceDefaultNote,
  repointChats,
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
