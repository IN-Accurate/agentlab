import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import App from './App.jsx';

const evidence = vi.hoisted(() => ({ target: null }));

vi.mock('./chat/ChatView.jsx', () => ({
  default: ({ onOpenEvidence }) => (
    <button type="button" onClick={() => onOpenEvidence(evidence.target)}>
      View evidence in Dashboard
    </button>
  ),
}));

vi.mock('./dashboard/DashboardView.jsx', () => ({
  default: ({ dash, patchDash, chatCredentialId }) => (
    <>
      <output aria-label="Dashboard selection">{JSON.stringify({ dash, chatCredentialId })}</output>
      <button type="button" onClick={() => patchDash({
        tab: 'runs',
        runId: 'old-run',
        resultId: 'old-result',
        runVersion: 'old-version',
        resultFilter: 'failed',
        splitFilter: 'holdout',
        traceVersion: 'old-version',
        traceStatus: 'error',
        traceId: 'old-trace',
        spanId: 'old-span',
        traceVerdict: false,
        review: { repairId: 'old-repair' },
        compareA: 'old-baseline',
        compareB: 'old-candidate',
      })}>Set previous Dashboard selections</button>
    </>
  ),
}));

const agent = (projectId) => ({
  id: projectId,
  projectId,
  credentialId: `key-${projectId}`,
  model: 'muse-spark-1.3',
  endpoint: 'https://api.meta.ai/v1',
  isDefault: projectId === 'project-a',
  connectedAt: Date.now(),
  expiresAt: Date.now() + 2 * 60 * 60 * 1000,
});

function currentSelection() {
  return JSON.parse(screen.getByLabelText('Dashboard selection').textContent);
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('matchMedia', vi.fn(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })));
  const agents = [agent('project-a'), agent('project-b'), agent('project-c')];
  localStorage.setItem('agentlab.agents.v1', JSON.stringify(agents));
  localStorage.setItem('agentlab.chats.v1', JSON.stringify(agents.slice(0, 2).map((connection) => ({
    id: `chat-${connection.projectId}`,
    title: `Chat ${connection.projectId}`,
    titleSet: true,
    connection,
    messages: [],
    streaming: false,
    createdAt: Date.now(),
  }))));
  localStorage.setItem('agentlab.ui.v1', JSON.stringify({
    view: 'dashboard',
    activeId: 'chat-project-b',
    dash: { tab: 'runs', projectId: 'project-a' },
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('chat evidence navigation', () => {
  it.each([
    ['the supplied project', 'project-c', 'project-c'],
    ['the active chat when no project is supplied', undefined, 'project-b'],
  ])('opens %s with the selected run/result and clears unrelated Dashboard state', async (_label, projectId, expectedProject) => {
    evidence.target = { projectId, runId: 'selected-run', resultId: 'selected-result' };
    render(<App />);

    await screen.findByLabelText('Dashboard selection', {}, { timeout: 5000 });
    expect(currentSelection().dash.projectId).toBe('project-a');
    fireEvent.click(screen.getByRole('button', { name: 'Set previous Dashboard selections' }));
    expect(currentSelection().dash.runVersion).toBe('old-version');

    fireEvent.click(screen.getByRole('button', { name: 'Chat project-b', exact: true }));
    fireEvent.click(await screen.findByRole('button', { name: 'View evidence in Dashboard' }, { timeout: 5000 }));

    await screen.findByLabelText('Dashboard selection');
    expect(currentSelection()).toEqual({
      dash: {
        tab: 'runs',
        projectId: expectedProject,
        runId: 'selected-run',
        resultId: 'selected-result',
        runVersion: 'all',
        resultFilter: 'all',
        splitFilter: 'all',
        traceVersion: 'all',
        traceStatus: 'all',
        traceId: null,
        spanId: null,
        traceVerdict: null,
        review: null,
        compareA: null,
        compareB: null,
      },
      chatCredentialId: `key-${expectedProject}`,
    });
  });
});
