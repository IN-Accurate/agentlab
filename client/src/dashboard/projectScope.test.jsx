import { useState } from 'react';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DashboardView from './DashboardView.jsx';
import { api } from '../api.js';
import { labelDuplicates, ownProjectIds } from './projectScope.js';

vi.mock('../api.js', () => ({ api: vi.fn(), proposeFix: vi.fn() }));
// Each test gets a fresh fetch cache, so one test's data never answers another's.
const cache = vi.hoisted(() => ({ current: null }));
vi.mock('../fetchCache.js', async (load) => {
  const real = await load();
  cache.current = real.createFetchCache();
  return {
    ...real,
    fetchCache: {
      getCached: (...args) => cache.current.getCached(...args),
      fetchShared: (...args) => cache.current.fetchShared(...args),
    },
  };
});
beforeEach(async () => {
  const { createFetchCache } = await vi.importActual('../fetchCache.js');
  cache.current = createFetchCache();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const PROJECTS = {
  mine: { id: 'mine', name: 'Billing agent' },
  member: { id: 'member', name: 'Refund helper' },
  someoneElse: { id: 'someone-else', name: 'Billing agent' },
};

function mockApi() {
  api.mockImplementation(async (path) => {
    const id = path.match(/^\/v1\/projects\/([^/?]+)$/)?.[1];
    if (id) {
      if (!PROJECTS[id === 'someone-else' ? 'someoneElse' : id]) {
        throw Object.assign(new Error('Not found'), { status: 404 });
      }
      return PROJECTS[id === 'someone-else' ? 'someoneElse' : id];
    }
    if (path === '/v1/projects?limit=100') return { items: Object.values(PROJECTS) };
    if (path.endsWith('/dashboard')) return { versions: [], runs: [] };
    return { items: [] };
  });
}

function Harness({ agents, start = {} }) {
  const [dash, setDash] = useState({ tab: 'overview', ...start });
  return (
    <>
      <output data-testid="selected">{dash.projectId ?? ''}</output>
      <DashboardView
        dash={dash}
        patchDash={(patch) => setDash((old) => ({ ...old, ...patch }))}
        agents={agents}
        onOpenAgentManager={() => {}}
      />
    </>
  );
}

describe('dashboard project picker', () => {
  it('lists only the agents saved in Agent Manager, including team members', async () => {
    mockApi();
    render(<Harness agents={[{ projectId: 'mine', memberProjectIds: ['member', 'deleted'] }]} />);
    const picker = await screen.findByRole('combobox', { name: 'Project' });
    const names = within(picker).getAllByRole('option').map((o) => o.textContent);
    expect(names).toEqual(['Billing agent', 'Refund helper']);
    expect(api.mock.calls.some(([path]) => path.startsWith('/v1/projects?'))).toBe(false);
  });

  it('moves a remembered project that is not the user’s to their first agent', async () => {
    mockApi();
    render(<Harness agents={[{ projectId: 'mine' }]} start={{ projectId: 'someone-else' }} />);
    await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('mine'));
    await waitFor(() =>
      expect(api.mock.calls.some(([path]) => path === '/v1/projects/mine/dashboard')).toBe(true),
    );
    expect(api.mock.calls.some(([path]) => path === '/v1/projects/someone-else/dashboard')).toBe(false);
  });

  it('shows an empty state when the user has no agents', async () => {
    mockApi();
    render(<Harness agents={[]} />);
    await screen.findByText('No agents yet');
  });
});

describe('project scope helpers', () => {
  it('collects agent and team member ids once each', () => {
    expect(
      ownProjectIds([{ projectId: 'a', memberProjectIds: ['b', 'a'] }, { projectId: 'b' }, {}]),
    ).toEqual(['a', 'b']);
  });

  it('tells same-named agents apart', () => {
    const labels = labelDuplicates([
      { id: 'abcdef12', name: 'Billing agent' },
      { id: '98765432', name: 'Billing agent' },
      { id: 'x', name: 'Shipping' },
    ]).map((p) => p.label);
    expect(labels).toEqual(['Billing agent · abcdef', 'Billing agent · 987654', 'Shipping']);
  });
});
