import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import RunsTab from './RunsTab.jsx';
import { api } from '../api.js';

vi.mock('../api.js', () => ({ api: vi.fn(), proposeFix: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const versions = [{ version_id: 'v1', label: 'v1' }, { version_id: 'v2', label: 'v2' }];
const run = (n) => ({ id: `run-${n}`, app_version_id: 'v1', created_at: `2026-11-04T12:00:0${n}Z` });

it('shows four runs per page with stable numbering, cached navigation, and filter reset', async () => {
  api.mockImplementation(async (path) => {
    if (path.startsWith('/v1/runs?')) {
      const params = new URLSearchParams(path.split('?')[1]);
      expect(params.get('limit')).toBe('4');
      expect(params.get('project_id')).toBe('pagination-project');
      if (params.get('version_id') === 'v2') return { items: [], next_cursor: null };
      return params.has('cursor')
        ? { items: [run(5), run(6)], next_cursor: null }
        : { items: [1, 2, 3, 4].map(run), next_cursor: 'page-two' };
    }
    if (path.endsWith('/summary')) return { version_id: 'v1', passed: 1, failed: 0, total: 1, dev: { passed: 1, total: 1 }, holdout: { passed: 0, total: 0 }, results: [] };
    return { items: [] };
  });
  function Harness() {
    const [dash, setDash] = useState({});
    return <RunsTab projectId="pagination-project" versions={versions} labels={{ v1: 'v1', v2: 'v2' }} dash={dash} patchDash={(patch) => setDash((old) => ({ ...old, ...patch }))} />;
  }
  render(<Harness />);
  const rail = screen.getByRole('complementary', { name: 'Run history' });
  await within(rail).findByRole('button', { name: /R-001/ });
  expect(within(rail).getAllByRole('listitem')).toHaveLength(4);
  expect(screen.getByRole('button', { name: 'Previous runs page' }).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Next runs page' }));
  await within(rail).findByRole('button', { name: /R-005/ });
  expect(within(rail).getAllByRole('listitem')).toHaveLength(2);
  expect(screen.getByText('Page 2')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Next runs page' }).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Previous runs page' }));
  await within(rail).findByRole('button', { name: /R-001/ });
  expect(within(rail).getAllByRole('listitem')).toHaveLength(4);
  fireEvent.click(screen.getByRole('button', { name: 'Next runs page' }));
  await within(rail).findByRole('button', { name: /R-005/ });
  expect(api.mock.calls.filter(([path]) => path.startsWith('/v1/runs?'))).toHaveLength(2);
  fireEvent.change(screen.getByRole('combobox', { name: 'Filter by version' }), { target: { value: 'v2' } });
  await screen.findByText('No runs yet.');
  expect(screen.getByText('Page 1')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Previous runs page' }).disabled).toBe(true);
});
