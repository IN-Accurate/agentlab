import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ChatView from './ChatView.jsx';
import Markdown from './Markdown.jsx';
import { api, streamChat } from '../api.js';

vi.mock('../api.js', () => ({ api: vi.fn(), proposeFix: vi.fn(), streamChat: vi.fn() }));

const connection = { projectId: 'project', credentialId: 'key', model: 'muse-spark' };
const agent = { ...connection, id: 'project', isDefault: true, expiresAt: Date.now() + 3600000 };
let emit;
let finish;
let reject;

function Harness({ savedAgents = [agent], ...props } = {}) {
  const [chat, setChat] = useState({ id: 'chat', title: 'New chat', connection, messages: [], streaming: false });
  return <ChatView chat={chat} agents={savedAgents} onPatch={(_id, update) => setChat(update)} {...props} />;
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', (callback) => setTimeout(callback, 0));
  vi.stubGlobal('cancelAnimationFrame', clearTimeout);
  streamChat.mockImplementation((_project, _body, onEvent) => {
    emit = onEvent;
    return new Promise((resolve, fail) => { finish = resolve; reject = fail; });
  });
});

afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

describe('chat interaction regressions', () => {
  it('reconnects an expired team member while keeping the healthy coordinator connected', async () => {
    const member = { ...agent, id: 'member', projectId: 'member', credentialId: 'member-key', isDefault: false };
    const onCredentialInvalid = vi.fn();
    const onReconnectAgent = vi.fn();
    api.mockImplementation(async (path) => {
      if (path.endsWith('/member-key')) throw { code: 'credential_expired' };
      return { credential_id: 'key' };
    });
    render(<Harness savedAgents={[{ ...agent, memberProjectIds: ['member'] }, member]} agentKeys={{ member: 'member-key' }} onCredentialInvalid={onCredentialInvalid} onReconnectAgent={onReconnectAgent} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'Team request' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await act(async () => { reject({ code: 'credential_expired', message: 'A team credential expired' }); });
    await waitFor(() => expect(onCredentialInvalid).toHaveBeenCalledWith('member-key'));
    expect(onCredentialInvalid).not.toHaveBeenCalledWith('key');
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect', exact: true }));
    expect(onReconnectAgent).toHaveBeenCalledWith('member');
  });
  it('keeps policy or context given earlier for the follow-up questions', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Add policy or context' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Optional policy or context' }), {
      target: { value: 'Company: ShopCore Logistics' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'what is company name' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await act(async () => { emit('message.delta', { text: 'ShopCore Logistics.' }); finish(); });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy());
    expect(screen.getByText(/Policy or context attached/)).toBeTruthy();
    // The draft test offered after the reply carries the same policy.
    expect(screen.getByRole('textbox', { name: 'Policy or context' }).value).toBe('Company: ShopCore Logistics');

    fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'what does this company do' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(streamChat).toHaveBeenCalledTimes(2));
    const follow = streamChat.mock.calls[1][1];
    expect(follow.message).toBe('what does this company do\n\nAdditional context:\nCompany: ShopCore Logistics');
    expect(follow.history.at(-1)).toEqual({ role: 'user', content: 'what does this company do' });
  });

  it('opens a long chat at its latest messages and loads earlier ones on request', () => {
    const messages = Array.from({ length: 120 }, (_, n) => ({
      id: `m${n}`, role: n % 2 ? 'assistant' : 'user', text: `message ${n}`, kind: 'chat', cards: [],
    }));
    function Long() {
      const [chat, setChat] = useState({ id: 'long', title: 'Long chat', connection, messages, streaming: false });
      return <ChatView chat={chat} agents={[agent]} onPatch={(_id, update) => setChat(update)} />;
    }
    render(<Long />);
    expect(screen.getAllByRole('article')).toHaveLength(40);
    expect(screen.getByText('message 119')).toBeTruthy();
    expect(screen.queryByText('message 79')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show earlier messages (80)' }));
    expect(screen.getAllByRole('article')).toHaveLength(80);
    fireEvent.click(screen.getByRole('button', { name: 'Show earlier messages (40)' }));
    expect(screen.getAllByRole('article')).toHaveLength(120);
    expect(screen.getByText('message 0')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Show earlier messages/ })).toBeNull();
  });

  it('labels test-only context differently from context kept for the chat', () => {
    const messages = [
      { id: 'c', role: 'user', kind: 'chat', text: 'Hi', context: 'Chat policy', cards: [] },
      { id: 'r', role: 'user', kind: 'run', text: 'Run test: x', context: 'Test policy', cards: [] },
    ];
    function Labels() {
      const [chat, setChat] = useState({ id: 'l', title: 'Labels', connection, messages, streaming: false });
      return <ChatView chat={chat} agents={[agent]} onPatch={(_id, update) => setChat(update)} />;
    }
    render(<Labels />);
    expect(screen.getByText('Policy or context attached · kept for this chat')).toBeTruthy();
    expect(screen.getByText('Policy or context used for this test')).toBeTruthy();
  });

  it('lets the user remove a selected suggestion before sending any message', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: /No made-up facts/ }));
    expect(screen.getByRole('button', { name: 'Remove No made-up facts' })).toBeTruthy();
    expect(screen.queryByRole('article')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Remove No made-up facts' }));
    expect(screen.queryByRole('button', { name: 'Run test' })).toBeNull();
    expect(screen.getByRole('region', { name: 'Suggested tests' })).toBeTruthy();
  });

  it('runs a selected test after the user message, hides suggestions, and formats its result', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: /No made-up facts/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Run test' }));
    expect(screen.getAllByRole('article').map((node) => node.getAttribute('aria-label')))
      .toEqual(['Your message', 'AgentLab response']);
    for (const message of screen.getAllByRole('article')) {
      const header = message.querySelector('.message-head');
      expect(header.firstElementChild.classList.contains('chat-avatar')).toBe(true);
      expect(header.lastElementChild.classList.contains('message-label')).toBe(true);
    }
    expect(screen.queryByRole('region', { name: 'Suggested tests' })).toBeNull();
    expect(screen.getByText('Thinking…')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Send' }).disabled).toBe(true);
    await act(async () => {
      emit('tool.result', { type: 'run_summary', data: { passed: true, expected: 'No invented facts', actual: 'I **can** help.\n\n- First\n- Second', explanation: 'The output is *grounded*.' } });
      emit('message.delta', { text: 'Test **complete**.' });
      finish();
    });
    expect(screen.getByText('Passed')).toBeTruthy();
    expect(screen.getByText('can').tagName).toBe('STRONG');
    expect(screen.getByText('grounded').tagName).toBe('EM');
    expect(screen.getAllByRole('listitem').some((node) => node.textContent === 'First')).toBe(true);
    expect(screen.queryByText('Thinking…')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Run test' })).toBeNull();
  });

  it('clears selected suggestions on an ordinary send and preserves cards across stream paints', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: /No made-up facts/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'Check this response' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(screen.queryByRole('button', { name: 'Remove No made-up facts' })).toBeNull();
    await act(async () => {
      emit('tool.result', { type: 'run_summary', data: { passed: true, expected: 'Safe output', actual: '**Valid** answer' } });
    });
    await waitFor(() => expect(screen.getByText('Passed')).toBeTruthy());
    await act(async () => {
      emit('message.delta', { text: 'A formatted ' });
    });
    await waitFor(() => expect(screen.getByText('A formatted')).toBeTruthy());
    await act(async () => {
      emit('message.delta', { text: '**reply**.' });
      finish();
    });
    expect(screen.getByText('Passed')).toBeTruthy();
    expect(screen.getByText('Valid').tagName).toBe('STRONG');
    expect(screen.getByText('reply').tagName).toBe('STRONG');
    expect(streamChat).toHaveBeenCalledTimes(1);
  });

  it('keeps a failed first request in user-first order and does not restore suggestions', async () => {
    render(<Harness />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'Hello' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await act(async () => { reject(new Error('Agent unavailable')); });
    expect(screen.getAllByRole('article')[0].getAttribute('aria-label')).toBe('Your message');
    expect(screen.queryByRole('region', { name: 'Suggested tests' })).toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('Agent unavailable');
    expect(screen.queryByRole('button', { name: 'Run test' })).toBeNull();
  });

  it('renders Markdown without executing HTML or allowing javascript links', () => {
    const { container } = render(<Markdown>{'**Bold** and *italic*\n\n`code`\n\n[unsafe](javascript:alert%281%29)\n\n<script>alert(1)</script>'}</Markdown>);
    expect(screen.getByText('Bold').tagName).toBe('STRONG');
    expect(screen.getByText('italic').tagName).toBe('EM');
    expect(screen.getByText('code').tagName).toBe('CODE');
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText('unsafe').getAttribute('href')).not.toContain('javascript:');
  });
});
