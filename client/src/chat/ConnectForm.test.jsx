import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ConnectForm from './ConnectForm.jsx';
import { api, ApiError } from '../api.js';

const controls = vi.hoisted(() => ({ defaults: { available: true, base_url: 'https://api.meta.ai/v1', model: 'muse-spark-1.3' } }));
vi.mock('../hooks.js', () => ({ useFetch: () => ({ data: controls.defaults, loading: false }) }));
vi.mock('../api.js', () => ({ api: vi.fn(), ApiError: class ApiError extends Error {} }));

const existing = {
  id: 'saved-project', projectId: 'saved-project', versionId: 'saved-version',
  name: 'Support agent', endpoint: 'https://api.meta.ai/v1', model: 'muse-spark-1.3',
  credentialId: 'expired-key', isDefault: true, credentialSource: 'default',
};
const expiresAt = '2026-11-05T02:00:00Z';

beforeEach(() => {
  vi.clearAllMocks();
  controls.defaults = { available: true, base_url: existing.endpoint, model: existing.model };
  api.mockImplementation(async (path) => {
    if (path.startsWith('/v1/credentials')) return { credential_id: 'fresh-key', expires_at: expiresAt };
    if (path.endsWith('/test-connection')) return { ok: true };
    if (path === '/v1/projects') return { id: 'new-project' };
    return { id: 'new-version' };
  });
});
afterEach(cleanup);

describe('connect and reconnect forms', () => {
  it('keeps the default checkbox and its text inside one aligned clickable label', async () => {
    const onConnect = vi.fn();
    render(<ConnectForm agents={[existing]} onConnect={onConnect} />);
    const checkbox = screen.getByRole('checkbox', { name: 'Set as default agent' });
    expect(checkbox.closest('label').classList.contains('default-agent-toggle')).toBe(true);
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);
    expect(screen.getByText('This will replace Support agent as your default.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Connect agent' }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledTimes(1));
    expect(onConnect.mock.calls[0][0]).toMatchObject({ expiresAt, credentialSource: 'default' });
    expect(onConnect.mock.calls[0][1]).toEqual({ setAsDefault: true });
  });

  it('offers to remove an agent whose project no longer exists instead of retrying forever', async () => {
    api.mockImplementation(async (path) => {
      if (path.startsWith('/v1/credentials')) return { credential_id: 'fresh-key', expires_at: expiresAt };
      if (path.endsWith('/test-connection')) {
        throw Object.assign(new ApiError('Record not found'), { status: 404, code: 'not_found' });
      }
      throw new Error(`unexpected ${path}`);
    });
    const onConnect = vi.fn();
    const onRemove = vi.fn();
    render(<ConnectForm agents={[existing]} reconnectAgent={existing} onConnect={onConnect} onRemove={onRemove} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect agent' }));
    await screen.findByText(/This agent no longer exists on the server/);
    expect(screen.queryByRole('button', { name: 'Reconnect agent' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Remove this agent' }));
    expect(onRemove).toHaveBeenCalledWith('saved-project');
    expect(onConnect).not.toHaveBeenCalled();
    expect(api.mock.calls.some(([path]) => path === '/v1/projects')).toBe(false);
  });

  it('reconnects the same project/version using a fresh server credential without creating replacements', async () => {
    const onConnect = vi.fn();
    render(<ConnectForm agents={[existing]} reconnectAgent={existing} onConnect={onConnect} />);
    expect(screen.getByRole('radio', { name: 'Default Meta key' }).checked).toBe(true);
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.getByLabelText('Agent name (optional)').readOnly).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect agent' }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledTimes(1));
    expect(api.mock.calls.map(([path]) => path)).toEqual([
      '/v1/credentials/default', '/v1/projects/saved-project/agent/test-connection',
    ]);
    expect(onConnect.mock.calls[0][0]).toMatchObject({ projectId: 'saved-project', versionId: 'saved-version', name: existing.name, credentialId: 'fresh-key', expiresAt, credentialSource: 'default' });
    expect(onConnect.mock.calls[0][1]).toEqual({ setAsDefault: false });
  });

  it('requires reentering an own key and keeps the existing endpoint/model', async () => {
    const onConnect = vi.fn();
    render(<ConnectForm agents={[existing]} reconnectAgent={{ ...existing, credentialSource: 'custom' }} onConnect={onConnect} />);
    expect(screen.getByRole('radio', { name: 'My own key' }).checked).toBe(true);
    expect(screen.getByLabelText('Endpoint').readOnly).toBe(true);
    expect(screen.getByLabelText('Model').readOnly).toBe(true);
    expect(screen.getByLabelText('API key').value).toBe('');
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'example-test-key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect agent' }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledTimes(1));
    const credentialCall = api.mock.calls[0];
    expect(credentialCall[0]).toBe('/v1/credentials');
    expect(JSON.parse(credentialCall[1].body)).toMatchObject({ base_url: existing.endpoint, model: existing.model, api_key: 'example-test-key' });
    expect(onConnect.mock.calls[0][0].credentialSource).toBe('custom');
  });

  it('does not offer a different server model when reconnecting an existing agent', () => {
    controls.defaults = { available: true, base_url: 'https://other.example/v1', model: 'other-model' };
    render(<ConnectForm agents={[existing]} reconnectAgent={existing} onConnect={vi.fn()} />);
    expect(screen.queryByRole('radio', { name: 'Default Meta key' })).toBeNull();
    expect(screen.getByLabelText('Endpoint').value).toBe(existing.endpoint);
    expect(screen.getByLabelText('API key')).toBeTruthy();
  });

  it('leaves the saved agent untouched on failure and obtains a fresh credential for a retry', async () => {
    const onConnect = vi.fn();
    let attempts = 0;
    api.mockImplementation(async (path) => path.endsWith('/test-connection')
      ? { ok: ++attempts > 1, error: 'Connection rejected' }
      : { credential_id: `fresh-${attempts}`, expires_at: expiresAt });
    render(<ConnectForm agents={[existing]} reconnectAgent={existing} onConnect={onConnect} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect agent' }));
    await screen.findByText('Connection rejected');
    expect(onConnect).not.toHaveBeenCalled();
    expect(existing.credentialId).toBe('expired-key');
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect agent' }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledTimes(1));
    expect(api.mock.calls.filter(([path]) => path === '/v1/credentials/default')).toHaveLength(2);
  });

  it('ignores a late credential response after the form is left', async () => {
    let finish;
    api.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const onConnect = vi.fn();
    const { unmount } = render(<ConnectForm agents={[existing]} reconnectAgent={existing} onConnect={onConnect} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect agent' }));
    unmount();
    await act(async () => finish({ credential_id: 'late-key', expires_at: expiresAt }));
    expect(onConnect).not.toHaveBeenCalled();
    expect(api).toHaveBeenCalledTimes(1);
  });
});
