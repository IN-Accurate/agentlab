import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import TraceWaterfall from './TraceWaterfall.jsx';

afterEach(cleanup);

const row = (id, type, offset, duration, depth = 0) => ({
  depth,
  node: { id, sdk_span_id: `sdk-${id}`, type, status: 'ok', started_at: new Date(offset).toISOString(), latency_ms: duration },
});

describe('TraceWaterfall', () => {
  it('exposes accurate shared-clock offsets/durations and selects the requested span', () => {
    const onSelect = vi.fn();
    render(<TraceWaterfall rows={[
      row('parent', 'agent', 1000, 2000),
      row('model', 'llm', 1200, 800, 1),
      row('tool', 'tool', 2200, 200, 1),
    ]} selectedId="model" onSelect={onSelect} />);
    const model = screen.getByRole('button', { name: /LLM · sdk-model, start \+200 ms, duration 800 ms/ });
    expect(model.getAttribute('aria-pressed')).toBe('true');
    expect(model.title).toContain('start +200 ms');
    fireEvent.click(screen.getByRole('button', { name: /Tool · sdk-tool/ }));
    expect(onSelect).toHaveBeenCalledWith('tool');
    expect(screen.getByText('Shared elapsed time: 2 s.')).toBeTruthy();
  });

  it('uses identical grid columns for tick axis and every row and renders continuous tree guides', () => {
    const { container } = render(<TraceWaterfall rows={[
      row('root', 'agent', 0, 2000),
      row('child', 'agent', 0, 1000, 1),
      row('grandchild', 'llm', 100, 500, 2),
      row('next', 'llm', 1100, 800, 1),
    ]} />);
    expect(container.querySelector('.trace-timeline-axis').classList.contains('trace-timeline-columns')).toBe(true);
    for (const button of screen.getAllByRole('button')) expect(button.classList.contains('trace-timeline-columns')).toBe(true);
    const grandchild = screen.getByRole('button', { name: /LLM · sdk-grandchild/ });
    expect(grandchild.querySelector('.trace-tree-line.ancestor')).toBeTruthy();
    expect(grandchild.querySelector('.trace-tree-branch.last')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Agent · sdk-root/ }).querySelector('.trace-tree-line.children')).toBeTruthy();
    expect(container.querySelector('.trace-tick.first').style.left).toBe('0%');
    expect(container.querySelector('.trace-tick.last').style.left).toBe('100%');
  });

  it('marks missing timing as estimated and keeps an instantaneous endpoint inside the track', () => {
    const { container } = render(<TraceWaterfall rows={[
      row('first', 'llm', 0, 1000),
      { node: { id: 'unknown', type: 'validation', latency_ms: 0 }, depth: 0 },
    ]} />);
    const estimated = screen.getByRole('button', { name: /Validation, start \+1 s \(estimated\), duration 0 ms/ });
    const bar = estimated.querySelector('.trace-timing-bar');
    expect(bar.classList.contains('estimated')).toBe(true);
    expect(parseFloat(bar.style.left) + parseFloat(bar.style.width)).toBeLessThanOrEqual(100);
    expect(container.textContent).toContain('Dashed bars indicate estimated timing');
  });
});
