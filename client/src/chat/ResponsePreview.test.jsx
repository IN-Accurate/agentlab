import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import ResponsePreview from './ResponsePreview.jsx';

const response = '**First sentence is bold.** Second sentence is *italic*. Third sentence is brief. Fourth sentence ends the preview. Fifth sentence belongs in the dialog.\n\n```js\nconst value = "<original>";\n```\n\n- Final detail';

afterEach(cleanup);

describe('ResponsePreview', () => {
  it('renders short responses with existing Markdown formatting', () => {
    render(<ResponsePreview className="muted">{'I **can** help.\n\n- First\n- Second'}</ResponsePreview>);
    expect(screen.getByText('can').tagName).toBe('STRONG');
    expect(screen.getAllByRole('listitem').map((node) => node.textContent)).toEqual(['First', 'Second']);
    expect(screen.queryByRole('button', { name: 'View full response' })).toBeNull();
    expect(screen.getByText('can').closest('.markdown').classList.contains('muted')).toBe(true);
  });

  it('shows a faithful four-sentence preview and the complete response in a dialog', () => {
    const evidence = vi.fn();
    render(<ResponsePreview action={<button onClick={evidence}>View evidence</button>}>{response}</ResponsePreview>);
    expect(screen.getByText('First sentence is bold.').tagName).toBe('STRONG');
    expect(screen.getByText('italic').tagName).toBe('EM');
    expect(document.body.textContent).not.toContain('Fifth sentence belongs in the dialog.');
    const open = screen.getByRole('button', { name: 'View full response' });
    open.focus();
    fireEvent.click(open);
    const dialog = screen.getByRole('dialog', { name: 'Full agent response' });
    expect(dialog.textContent).toContain('Fifth sentence belongs in the dialog.');
    expect(within(dialog).getByText('const value = "<original>";').tagName).toBe('CODE');
    expect(within(dialog).getByText('Final detail').tagName).toBe('LI');
    fireEvent.click(within(dialog).getByRole('button', { name: 'View evidence' }));
    expect(evidence).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(open);
  });

  it('keeps an open dialog current as the response streams', () => {
    const { rerender } = render(<ResponsePreview>{response}</ResponsePreview>);
    fireEvent.click(screen.getByRole('button', { name: 'View full response' }));
    rerender(<ResponsePreview>{`${response}\n\nA newly streamed ending.`}</ResponsePreview>);
    expect(screen.getByRole('dialog').textContent).toContain('A newly streamed ending.');
  });

  it('caps an enormous unpunctuated response and keeps unsafe HTML and URLs disabled', () => {
    const text = `${'word '.repeat(500)}\n\n[unsafe](javascript:alert%281%29)\n\n<script>alert(1)</script>`;
    const { container } = render(<ResponsePreview>{text}</ResponsePreview>);
    expect(container.querySelector('.response-preview-content').textContent.length).toBeLessThanOrEqual(900);
    fireEvent.click(screen.getByRole('button', { name: 'View full response' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog.querySelector('script')).toBeNull();
    expect(within(dialog).getByText('unsafe').getAttribute('href')).not.toContain('javascript:');
  });

  it('renders an absent response without a disclosure button', () => {
    const { container } = render(<ResponsePreview>{undefined}</ResponsePreview>);
    expect(container.textContent).toBe('');
    expect(screen.queryByRole('button')).toBeNull();
  });
});
