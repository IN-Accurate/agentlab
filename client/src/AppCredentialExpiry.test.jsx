import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from './App.jsx';
import { api, streamChat } from './api.js';

vi.mock('./api.js', () => ({ api: vi.fn(), streamChat: vi.fn(), proposeFix: vi.fn(), pollJob: vi.fn(), ApiError: class ApiError extends Error {} }));

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', (callback) => setTimeout(callback, 0));
  vi.stubGlobal('cancelAnimationFrame', clearTimeout);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

describe('credential expiry recovery', () => {
  it.each(['http', 'stream'])('marks a %s-expired credential disconnected and reconnects the same chat with a fresh key', async (failureMode) => {
    const expiresAt = new Date(Date.now() + 3600000).toISOString();
    const agent = { id: 'project', projectId: 'project', versionId: 'version', name: 'Support agent', model: 'muse-spark', endpoint: 'https://api.meta.ai/v1', credentialId: 'old-key', credentialSource: 'default', isDefault: true, expiresAt: Date.now() + 7200000 };
    localStorage.setItem('agentlab.agents.v1', JSON.stringify([agent]));
    localStorage.setItem('agentlab.chats.v1', JSON.stringify([{ id: 'history', agentId: agent.id, title: 'Existing chat', titleSet: true, connection: agent, messages: [] }]));
    localStorage.setItem('agentlab.ui.v1', JSON.stringify({ view: 'chat', activeId: 'history' }));
    const expired = { code: 'credential_expired', message: 'Credential is expired, revoked or unknown' };
    if (failureMode === 'http') streamChat.mockRejectedValueOnce(expired);
    else streamChat.mockImplementationOnce(async (_project, _body, emit) => { emit('error', { error: expired }); });
    streamChat.mockImplementationOnce(async (_project, _body, emit) => { emit('message.delta', { text: 'The renewed connection works.' }); });
    api.mockImplementation(async (path, options) => {
      if (path === '/v1/credentials/default') return options?.method === 'POST'
        ? { credential_id: 'fresh-key', expires_at: expiresAt }
        : { available: true, base_url: agent.endpoint, model: agent.model };
      if (path.endsWith('/test-connection')) return { ok: true };
      if (path === '/v1/credentials/old-key') throw expired;
      if (path === '/v1/credentials/fresh-key') return { credential_id: 'fresh-key', expires_at: expiresAt };
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<App />);
    const message = await screen.findByRole('textbox', { name: 'Message' }, { timeout: 5000 });
    fireEvent.change(message, { target: { value: 'First request' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain(expired.message);
    expect(screen.getByRole('button', { name: /Agent Manager Not connected/ })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Message' }).disabled).toBe(true);
    fireEvent.click(within(alert).getByRole('button', { name: 'Reconnect', exact: true }));
    await screen.findByRole('heading', { name: 'Reconnect agent' }, { timeout: 5000 });
    await screen.findByRole('radio', { name: 'Default Meta key' });
    expect(screen.getByLabelText('Agent name (optional)').value).toBe('Support agent');
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect agent', exact: true }));
    await screen.findByText('Reconnected.');
    fireEvent.click(screen.getByRole('button', { name: 'Existing chat', exact: true }));
    const renewedMessage = await screen.findByRole('textbox', { name: 'Message' });
    expect(renewedMessage.disabled).toBe(false);
    expect(screen.getByText('First request')).toBeTruthy();
    fireEvent.change(renewedMessage, { target: { value: 'Try again' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('The renewed connection works.');
    expect(streamChat.mock.calls[1][0]).toBe('project');
    expect(streamChat.mock.calls[1][1].credential_id).toBe('fresh-key');
    expect(api.mock.calls.some(([path]) => path === '/v1/projects' || path.endsWith('/versions'))).toBe(false);
    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('agentlab.agents.v1'));
      expect(stored).toHaveLength(1);
      expect(stored[0]).toMatchObject({ credentialId: 'fresh-key', expiresAt: Date.parse(expiresAt), isDefault: true, credentialInvalid: false });
    });
  });
});
