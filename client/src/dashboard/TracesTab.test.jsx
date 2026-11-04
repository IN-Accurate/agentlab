import { useState } from 'react';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TracesTab from './TracesTab.jsx';
import { useTraceRequests } from './useTraceRequests.js';
import { api } from '../api.js';

vi.mock('../api.js', () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
let sequence = 0;
const nextProject = () => `request-project-${sequence++}`;
const request = (id) => ({ id, input: `Request ${id}`, status: 'ok', app_version_id: 'v1' });

describe('request pagination', () => {
  it('shows four requests per page and selects the new page first request', async () => {
    const projectId = nextProject();
    api.mockImplementation(async (path) => {
      if (path.startsWith('/v1/traces?')) {
        const params = new URLSearchParams(path.split('?')[1]);
        expect(params.get('limit')).toBe('4');
        expect(params.get('project_id')).toBe(projectId);
        return params.has('cursor')
          ? { items: [request('5'), request('6')], next_cursor: null }
          : { items: ['1', '2', '3', '4'].map(request), next_cursor: 'page-two' };
      }
      const id = path.split('/').at(-1);
      return { id, status: 'ok', input: `Input ${id}`, output: 'Answer', span_tree: [] };
    });
    function Harness() {
      const [dash, setDash] = useState({});
      return <TracesTab projectId={projectId} versions={[{ version_id: 'v1', label: 'v1' }]} labels={{ v1: 'v1' }} dash={dash} patchDash={(patch) => setDash((old) => ({ ...old, ...patch }))} />;
    }
    render(<Harness />);
    const rail = screen.getByRole('complementary', { name: 'Requests' });
    await within(rail).findByRole('button', { name: /Request 1/ });
    expect(within(rail).getAllByRole('listitem')).toHaveLength(4);
    expect(screen.getByRole('button', { name: 'Previous requests page' }).disabled).toBe(true);
    fireEvent.click(within(rail).getByRole('button', { name: /Request 3/ }));
    await screen.findByText('Input 3');
    fireEvent.click(screen.getByRole('button', { name: 'Next requests page' }));
    await within(rail).findByRole('button', { name: /Request 5/ });
    expect(within(rail).getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText('Page 2')).toBeTruthy();
    await screen.findByText('Input 5');
    expect(screen.getByRole('button', { name: 'Next requests page' }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Previous requests page' }));
    await within(rail).findByRole('button', { name: /Request 1/ });
    expect(within(rail).getAllByRole('listitem')).toHaveLength(4);
    expect(api.mock.calls.filter(([path]) => path.startsWith('/v1/traces?'))).toHaveLength(2);
  });

  it('resets to page one when filters change and ignores a slow prior page', async () => {
    const projectId = nextProject();
    let finishOld;
    api.mockImplementation((path) => {
      const params = new URLSearchParams(path.split('?')[1]);
      if (params.has('status')) return Promise.resolve({ items: [request('error')], next_cursor: null });
      if (params.has('cursor')) return new Promise((resolve) => { finishOld = resolve; });
      return Promise.resolve({ items: [request('first')], next_cursor: 'next' });
    });
    const { result, rerender } = renderHook(({ status }) => useTraceRequests(projectId, 'all', status), { initialProps: { status: 'all' } });
    await waitFor(() => expect(result.current.items).toHaveLength(1));
    act(() => result.current.next());
    await waitFor(() => expect(finishOld).toBeTypeOf('function'));
    expect(result.current.page).toBe(2);
    rerender({ status: 'error' });
    expect(result.current.page).toBe(1);
    await waitFor(() => expect(result.current.items[0]?.id).toBe('error'));
    await act(async () => finishOld({ items: [request('late')], next_cursor: null }));
    expect(result.current.items[0].id).toBe('error');
    expect(result.current.hasPrevious).toBe(false);
  });

  it('keeps a linked request selected even when it is outside the current list page', async () => {
    const projectId = nextProject();
    api.mockImplementation(async (path) => path.includes('?')
      ? { items: [request('first')], next_cursor: null }
      : { status: 'ok', input: 'Linked request input', output: 'Answer', span_tree: [{ id: 'linked-span', sdk_span_id: 'linked-span', type: 'llm', latency_ms: 20, started_at: '2026-01-01T00:00:00Z', children: [] }] });
    render(<TracesTab projectId={projectId} versions={[]} labels={{}} dash={{ traceId: 'linked', spanId: 'old-trace-span' }} patchDash={vi.fn()} />);
    await screen.findByText('Linked request input');
    expect(api).toHaveBeenCalledWith('/v1/traces/linked');
    expect(screen.getByRole('button', { name: /LLM · linked-span, start \+0 ms/ }).getAttribute('aria-pressed')).toBe('true');
  });
});
