import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import Composer from './Composer.jsx';

beforeEach(() => {
  vi.stubGlobal('innerHeight', 900);
  // jsdom has no layout engine; emulate wrapped/multiline content measurements.
  vi.spyOn(HTMLTextAreaElement.prototype, 'scrollHeight', 'get').mockImplementation(function () {
    return Math.max(1, this.value.split('\n').length) * 24;
  });
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('Composer', () => {
  it('starts at one row, grows and shrinks with content, and scrolls after the height cap', () => {
    render(<Composer onSend={vi.fn()} />);
    const message = screen.getByRole('textbox', { name: 'Message' });
    expect(message.rows).toBe(1);
    expect(message.style.height).toBe('24px');
    fireEvent.change(message, { target: { value: 'one\ntwo\nthree' } });
    expect(message.style.height).toBe('72px');
    expect(message.style.overflowY).toBe('hidden');
    fireEvent.change(message, { target: { value: Array(12).fill('line').join('\n') } });
    expect(message.style.height).toBe('160px');
    expect(message.style.overflowY).toBe('auto');
    fireEvent.change(message, { target: { value: 'short again' } });
    expect(message.style.height).toBe('24px');
    expect(message.style.overflowY).toBe('hidden');
  });

  it('limits field height to a quarter of a small viewport', () => {
    vi.stubGlobal('innerHeight', 400);
    render(<Composer onSend={vi.fn()} />);
    const message = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(message, { target: { value: Array(12).fill('line').join('\n') } });
    expect(message.style.height).toBe('100px');
    expect(message.style.overflowY).toBe('auto');
    vi.stubGlobal('innerHeight', 600);
    fireEvent(window, new Event('resize'));
    expect(message.style.height).toBe('150px');
  });

  it('autosizes context and resets both inputs and message height after sending', () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add policy or context' }));
    const context = screen.getByRole('textbox', { name: 'Optional policy or context' });
    expect(context.rows).toBe(1);
    fireEvent.change(context, { target: { value: Array(10).fill('policy').join('\n') } });
    expect(context.style.height).toBe('160px');
    expect(context.style.overflowY).toBe('auto');
    const message = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(message, { target: { value: 'first\nsecond' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onSend).toHaveBeenCalledWith('first\nsecond', Array(10).fill('policy').join('\n'));
    expect(message.value).toBe('');
    expect(message.style.height).toBe('24px');
    expect(message.style.overflowY).toBe('hidden');
    expect(screen.queryByRole('textbox', { name: 'Optional policy or context' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add policy or context' }));
    expect(screen.getByRole('textbox', { name: 'Optional policy or context' }).value).toBe('');
  });

  it('preserves Shift+Enter and IME composition while Enter sends', () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);
    const message = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(message, { target: { value: ' hello ' } });
    expect(fireEvent.keyDown(message, { key: 'Enter', shiftKey: true })).toBe(true);
    expect(fireEvent.keyDown(message, { key: 'Enter', isComposing: true })).toBe(true);
    expect(fireEvent.keyDown(message, { key: 'Enter', keyCode: 229 })).toBe(true);
    expect(onSend).not.toHaveBeenCalled();
    expect(fireEvent.keyDown(message, { key: 'Enter' })).toBe(false);
    expect(onSend).toHaveBeenCalledWith('hello', '');
    expect(message.value).toBe('');
  });

  it('retains the context toggle and supports sending from the context field', () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'message' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add policy or context' }));
    const context = screen.getByRole('textbox', { name: 'Optional policy or context' });
    fireEvent.change(context, { target: { value: 'context' } });
    fireEvent.keyDown(context, { key: 'Enter', isComposing: true });
    fireEvent.keyDown(context, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Hide policy or context' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add policy or context' }));
    const reopened = screen.getByRole('textbox', { name: 'Optional policy or context' });
    expect(reopened.value).toBe('context');
    fireEvent.keyDown(reopened, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('message', 'context');
  });

  it('keeps a disabled composer and whitespace-only messages unsendable', () => {
    const onSend = vi.fn();
    const { rerender } = render(<Composer onSend={onSend} />);
    const message = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(message, { target: { value: '  \n  ' } });
    expect(screen.getByRole('button', { name: 'Send' }).disabled).toBe(true);
    fireEvent.keyDown(message, { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.change(message, { target: { value: 'draft' } });
    rerender(<Composer onSend={onSend} disabled />);
    expect(message.disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Send' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Add policy or context' }).disabled).toBe(true);
    fireEvent.keyDown(message, { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
  });
});
