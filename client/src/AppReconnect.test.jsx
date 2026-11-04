import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from './App.jsx';
import { api } from './api.js';

const controls = vi.hoisted(() => ({ chat: null, expiresAt: 0 }));

vi.mock('./api.js', () => ({ api: vi.fn() }));
vi.mock('./chat/ChatView.jsx', () => ({
  default: (props) => {
    controls.chat = props;
    return <>
      <output aria-label="Chat snapshot">{JSON.stringify({ chat: props.chat, agents: props.agents })}</output>
      <button onClick={() => props.onCredentialInvalid('old-key')}>Report expired credential</button>
      <button onClick={() => props.onReconnectAgent(props.chat.agentId)}>Reconnect from chat</button>
    </>;
  },
}));
vi.mock('./chat/ConnectForm.jsx', () => ({
  default: ({ reconnectAgent, onConnect, onCancel }) => <>
    <output aria-label="Reconnect target">{JSON.stringify(reconnectAgent)}</output>
    <button onClick={() => onConnect({ ...reconnectAgent, credentialId: 'fresh-key', expiresAt: controls.expiresAt }, { setAsDefault: true })}>Complete reconnect</button>
    <button onClick={onCancel}>Cancel reconnect</button>
  </>,
}));

function savedAgent(id = 'project-a', overrides = {}) {
  return {
    id, projectId: id, versionId: `version-${id}`, name: `Agent ${id}`,
    model: 'muse-spark-1.3', endpoint: 'https://api.meta.ai/v1',
    credentialId: id === 'project-a' ? 'old-key' : `key-${id}`,
    isDefault: id === 'project-a', connectedAt: Date.now(),
    expiresAt: Date.now() + 7_200_000, ...overrides,
  };
}

function setup({ view = 'agents', agents = [savedAgent()] } = {}) {
  localStorage.setItem('agentlab.agents.v1', JSON.stringify(agents));
  localStorage.setItem('agentlab.chats.v1', JSON.stringify([
    { id: 'history-a', title: 'First history', titleSet: true, agentId: 'project-a', connection: agents.find((a) => a.id === 'project-a'), messages: [{ id: 'm1', role: 'user', text: 'Existing history', cards: [] }] },
    { id: 'history-b', title: 'Second history', titleSet: true, agentId: 'project-a', connection: agents.find((a) => a.id === 'project-a'), messages: [] },
  ]));
  localStorage.setItem('agentlab.ui.v1', JSON.stringify({ view, activeId: 'history-a' }));
}

function agentCard(name = 'Agent project-a') {
  return screen.getByRole('region', { name });
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  controls.expiresAt = Date.now() + 60_000;
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  api.mockImplementation((path) => Promise.resolve({ credential_id: path.split('/').at(-1), expires_at: new Date(controls.expiresAt).toISOString() }));
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

describe('saved agent reconnect and credential status', () => {
  it('offers Reconnect for a connected agent and preserves all existing chats, project, version, and default', async () => {
    setup();
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reconnect', exact: true }, { timeout: 5000 }));
    const target = JSON.parse((await screen.findByLabelText('Reconnect target', {}, { timeout: 5000 })).textContent);
    expect(target.projectId).toBe('project-a');
    expect(target.versionId).toBe('version-project-a');
    fireEvent.click(screen.getByRole('button', { name: 'Complete reconnect' }));
    expect(screen.getByText('Reconnected.')).toBeTruthy();
    expect(within(agentCard()).getByText('Connected · Default')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'First history', exact: true }));
    const snapshot = JSON.parse((await screen.findByLabelText('Chat snapshot', {}, { timeout: 5000 })).textContent);
    expect(snapshot.chat.id).toBe('history-a');
    expect(snapshot.chat.messages[0].text).toBe('Existing history');
    expect(snapshot.chat.connection.credentialId).toBe('fresh-key');
    expect(snapshot.chat.connection.versionId).toBe('version-project-a');
    expect(snapshot.agents).toHaveLength(1);
    expect(snapshot.agents[0].isDefault).toBe(true);
    expect(snapshot.agents[0].expiresAt).toBe(controls.expiresAt);
    fireEvent.click(screen.getByRole('button', { name: 'Second history', exact: true }));
    expect(JSON.parse(screen.getByLabelText('Chat snapshot').textContent).chat.connection.credentialId).toBe('fresh-key');
    expect(within(screen.getByRole('navigation', { name: 'Recent chats' })).getAllByRole('listitem')).toHaveLength(2);
    expect(api.mock.calls.every(([path]) => path.startsWith('/v1/credentials/'))).toBe(true);
  });

  it('does not change the default when reconnecting a different saved agent', async () => {
    setup({ agents: [savedAgent(), savedAgent('project-b')] });
    render(<App />);
    const card = await screen.findByRole('region', { name: 'Agent project-b' });
    fireEvent.click(within(card).getByRole('button', { name: 'Reconnect', exact: true }));
    await screen.findByLabelText('Reconnect target', {}, { timeout: 5000 });
    fireEvent.click(screen.getByRole('button', { name: 'Complete reconnect' }));
    expect(within(agentCard()).getByText('Connected · Default')).toBeTruthy();
    expect(within(agentCard('Agent project-b')).getByRole('button', { name: 'Set as default' })).toBeTruthy();
  });

  it('reconciles expired or revoked backend credentials on Manager entry and retains Reconnect', async () => {
    setup();
    api.mockRejectedValue({ status: 404, code: 'credential_expired' });
    render(<App />);
    await waitFor(() => expect(within(agentCard()).getByText('Not connected · Default')).toBeTruthy());
    expect(within(agentCard()).getByRole('button', { name: 'Reconnect', exact: true }).disabled).toBe(false);
    expect(screen.queryByRole('button', { name: 'New chat', exact: true })).toBeNull();
  });

  it('keeps connection status on transient metadata failures and rechecks on window focus', async () => {
    setup();
    api.mockRejectedValueOnce(new TypeError('Network unavailable'));
    render(<App />);
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
    expect(within(agentCard()).getByText('Connected · Default')).toBeTruthy();
    api.mockResolvedValueOnce({ expires_at: new Date(Date.now() - 1).toISOString() });
    await act(async () => { window.dispatchEvent(new Event('focus')); });
    await waitFor(() => expect(within(agentCard()).getByText('Not connected · Default')).toBeTruthy());
    expect(api).toHaveBeenCalledTimes(2);
  });

  it('invalidates a chat credential immediately and ignores late errors after reconnect', async () => {
    setup({ view: 'chat' });
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'Report expired credential' }, { timeout: 5000 }));
    const oldCallback = controls.chat.onCredentialInvalid;
    expect(JSON.parse(screen.getByLabelText('Chat snapshot').textContent).agents[0].credentialInvalid).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect from chat' }));
    await screen.findByLabelText('Reconnect target', {}, { timeout: 5000 });
    fireEvent.click(screen.getByRole('button', { name: 'Complete reconnect' }));
    act(() => oldCallback('old-key'));
    expect(within(agentCard()).getByText('Connected · Default')).toBeTruthy();
  });

  it('ignores an old in-flight metadata failure after the agent has reconnected', async () => {
    setup();
    let rejectOld;
    api.mockImplementation((path) => path.endsWith('/old-key')
      ? new Promise((_resolve, reject) => { rejectOld = reject; })
      : Promise.resolve({ credential_id: 'fresh-key', expires_at: new Date(controls.expiresAt).toISOString() }));
    render(<App />);
    await waitFor(() => expect(rejectOld).toBeTypeOf('function'));
    fireEvent.click(within(agentCard()).getByRole('button', { name: 'Reconnect', exact: true }));
    await screen.findByLabelText('Reconnect target', {}, { timeout: 5000 });
    fireEvent.click(screen.getByRole('button', { name: 'Complete reconnect' }));
    await act(async () => { rejectOld({ status: 404, code: 'credential_expired' }); });
    expect(within(agentCard()).getByText('Connected · Default')).toBeTruthy();
  });
});
