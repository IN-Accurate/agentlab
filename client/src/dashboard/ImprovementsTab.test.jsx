import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ImprovementsTab from './ImprovementsTab.jsx';
import { api } from '../api.js';

vi.mock('../api.js', () => ({ api: vi.fn(), pollJob: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('loads a diagnosis without a repair yet without breaking the rules of hooks', async () => {
  // Hooks declared after the loading return crashed this tab ("Rendered more hooks
  // than during the previous render"), so Find a fix froze on "Starting…".
  api.mockImplementation(async (path) => {
    if (path.startsWith('/v1/repairs?')) return { items: [], next_cursor: null };
    if (path === '/v1/diagnoses/d1') return { id: 'd1', rule: 'instruction_violation', confidence: 0.6, evidence: {}, trace_id: null };
    throw new Error(`unexpected ${path}`);
  });
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  render(
    <ImprovementsTab projectId="p-hooks" labels={{}} dash={{ review: { diagnosisId: 'd1' } }} patchDash={() => {}} chatCredentialId="key" />,
  );
  expect(await screen.findByText(/Diagnosis · instruction_violation/)).toBeTruthy();
  expect(errors.mock.calls.flat().join(' ')).not.toMatch(/order of Hooks|more hooks/);
});
