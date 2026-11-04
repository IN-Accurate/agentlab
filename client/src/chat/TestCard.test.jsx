import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TestCard from './TestCard.jsx';

afterEach(cleanup);

const actual = [
  'The agent **cannot verify** that record.',
  'It acknowledges the missing source.',
  'It offers a useful next step.',
  'It asks for the original publication.',
  'This final sentence is preserved in the full response.',
].join(' ');

function result(overrides = {}) {
  return {
    id: 'card', kind: 'test', status: 'done',
    result: { verdict: 'pass', expected: 'Avoid invented facts', actual, runId: 'run', resultId: 'result', ...overrides },
  };
}

describe('test result presentation', () => {
  it('puts the evidence action beside the verdict before the response', () => {
    const onViewEvidence = vi.fn();
    const { container } = render(<TestCard card={result()} onViewEvidence={onViewEvidence} />);
    const action = screen.getByRole('button', { name: 'View evidence in Dashboard' });
    expect(action.closest('.test-result-head').textContent).toContain('Passed');
    const responseCaption = screen.getByText('Agent response');
    expect(action.compareDocumentPosition(responseCaption) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.textContent).not.toContain('This final sentence');
    fireEvent.click(action);
    expect(onViewEvidence).toHaveBeenCalledTimes(1);
  });

  it('opens the full Markdown response with its evidence action available in the dialog', () => {
    const onViewEvidence = vi.fn();
    render(<TestCard card={result()} onViewEvidence={onViewEvidence} />);
    const expand = screen.getByRole('button', { name: /View full response/ });
    expand.focus();
    fireEvent.click(expand);
    const dialog = screen.getByRole('dialog', { name: 'Full agent response' });
    expect(dialog.textContent).toContain('This final sentence is preserved in the full response.');
    expect(within(dialog).getByText('cannot verify').tagName).toBe('STRONG');
    fireEvent.click(within(dialog).getByRole('button', { name: 'View evidence in Dashboard' }));
    expect(onViewEvidence).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(expand);
  });

  it('does not navigate to unrelated evidence when a result has no saved identifiers', () => {
    const onViewEvidence = vi.fn();
    render(<TestCard card={result({ runId: null, resultId: null })} onViewEvidence={onViewEvidence} />);
    const action = screen.getByRole('button', { name: 'View evidence in Dashboard' });
    expect(action.disabled).toBe(true);
    fireEvent.click(action);
    expect(onViewEvidence).not.toHaveBeenCalled();
  });
});
