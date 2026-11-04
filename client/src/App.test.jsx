import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import App from './App.jsx';
import { api, streamChat } from './api.js';

vi.mock('./api.js', () => ({
  api: vi.fn(),
  streamChat: vi.fn(),
  proposeFix: vi.fn(),
  pollJob: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

const agent = {
  projectId: 'saved-project',
  credentialId: 'saved-credential',
  model: 'muse-spark-1.3',
  endpoint: 'https://api.meta.ai/v1',
};

function draft(id, title = 'New chat', overrides = {}) {
  return {
    id,
    title,
    titleSet: false,
    createdAt: Date.now(),
    connection: null,
    messages: [],
    streaming: false,
    ...overrides,
  };
}

function saveChats(chats, activeId = chats[0]?.id) {
  localStorage.setItem('agentlab.agents.v1', JSON.stringify([agent]));
  localStorage.setItem('agentlab.chats.v1', JSON.stringify(chats));
  localStorage.setItem('agentlab.ui.v1', JSON.stringify({ view: 'chat', activeId }));
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  vi.stubGlobal('matchMedia', vi.fn(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })));
  vi.stubGlobal('ResizeObserver', class ResizeObserver {
    observe() {}
    disconnect() {}
  });
  vi.stubGlobal('requestAnimationFrame', (callback) => setTimeout(callback, 0));
  vi.stubGlobal('cancelAnimationFrame', clearTimeout);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('saved agent and chat state', () => {
  it.each([
    ['a new default draft', []],
    ['a restored unassigned draft', [draft('restored-draft')]],
  ])('connects %s without needing another New chat click', async (_label, chats) => {
    saveChats(chats);

    render(<App />);

    const message = await screen.findByRole('textbox', { name: 'Message' }, { timeout: 5000 });
    expect(message.disabled).toBe(false);
    expect(screen.getByText(`Agent: ${agent.model}`)).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'What should we test?' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Connect an agent to get started' })).toBeNull();
    expect(api).not.toHaveBeenCalled();
    expect(streamChat).not.toHaveBeenCalled();
  });

  it('clears an unsent message, context, and selected suggestion when switching chats', async () => {
    saveChats([draft('first', 'First chat'), draft('second', 'Second chat')]);
    render(<App />);

    const message = await screen.findByRole('textbox', { name: 'Message' }, { timeout: 5000 });
    fireEvent.click(screen.getByRole('button', { name: /^No made-up facts/ }));
    expect(screen.getByRole('button', { name: 'Remove No made-up facts' })).toBeTruthy();
    fireEvent.change(message, { target: { value: 'An unsent message' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add policy or context' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Optional policy or context' }), {
      target: { value: 'An unsent context' },
    });

    const recentChats = screen.getByRole('navigation', { name: 'Recent chats' });
    fireEvent.click(within(recentChats).getByRole('button', { name: 'Second chat' }));

    expect(screen.getByRole('heading', { name: 'Second chat' })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Message' }).value).toBe('');
    expect(screen.queryByRole('textbox', { name: 'Optional policy or context' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Remove No made-up facts' })).toBeNull();

    fireEvent.click(within(recentChats).getByRole('button', { name: 'First chat' }));
    expect(screen.getByRole('textbox', { name: 'Message' }).value).toBe('');
    expect(screen.queryByRole('button', { name: 'Remove No made-up facts' })).toBeNull();
    expect(streamChat).not.toHaveBeenCalled();
  });

  it('keeps the saved agent when deleting the last conversation', async () => {
    saveChats([draft('last', 'Previous conversation', {
      titleSet: true,
      connection: agent,
      messages: [{ id: 'old-user', role: 'user', text: 'Previous message', cards: [] }],
    })]);
    render(<App />);
    await screen.findByRole('textbox', { name: 'Message' }, { timeout: 5000 });

    fireEvent.click(screen.getByRole('button', { name: 'Delete Previous conversation' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete', exact: true }));

    expect(screen.getByRole('heading', { name: 'New chat' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'What should we test?' })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Message' }).disabled).toBe(false);
    expect(screen.getByText(`Agent: ${agent.model}`)).toBeTruthy();
    expect(screen.queryByText('Previous message')).toBeNull();
    expect(api).not.toHaveBeenCalled();
    expect(streamChat).not.toHaveBeenCalled();
  });
});
