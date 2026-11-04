import { describe, expect, it } from 'vitest';
import { chartTicks } from './OverviewTab.jsx';

const labels = (d0, d1) => chartTicks(d0, d1).map((t) => Math.round(t * 100));

describe('pass-rate chart ticks', () => {
  it('labels a narrow range with every whole percent, evenly spaced', () => {
    expect(labels(0.95, 1)).toEqual([95, 96, 97, 98, 99, 100]);
  });

  it('labels wider ranges with even steps and keeps both ends', () => {
    expect(labels(0.5, 1)).toEqual([50, 60, 70, 80, 90, 100]);
    expect(labels(0, 1)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(labels(0.85, 1)).toEqual([85, 90, 95, 100]);
  });

  it('places each label exactly on its tick', () => {
    for (const [d0, d1] of [[0.95, 1], [0.6, 0.75], [0, 1]]) {
      for (const t of chartTicks(d0, d1)) expect(Math.round(t * 100)).toBeCloseTo(t * 100, 9);
    }
  });
});
