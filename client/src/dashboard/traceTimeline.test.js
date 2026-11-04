import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { barGeometry, buildTraceTimeline } from './traceTimeline.js';

const row = (id, start, latency, depth = 0) => ({
  node: { id, started_at: start, latency_ms: latency, type: 'llm' }, depth,
});
const at = (ms) => new Date(ms).toISOString();

describe('shared trace timeline', () => {
  it('preserves measured gaps and overlaps against the earliest finite timestamp', () => {
    const timeline = buildTraceTimeline([
      row('first', at(1000), 1000),
      row('overlap', at(1500), 750),
      row('gap', at(3000), 500),
    ]);
    assert.deepEqual(timeline.items.map(({ startMs, durationMs }) => [startMs, durationMs]), [[0, 1000], [500, 750], [2000, 500]]);
    assert.equal(timeline.totalMs, 2500);
    assert.deepEqual(timeline.ticks.map(({ value }) => value), [0, 625, 1250, 1875, 2500]);
    assert.equal(timeline.hasEstimatedTiming, false);
  });

  it('uses elapsed extent rather than summing nested spans or doubling their offsets', () => {
    const timeline = buildTraceTimeline([
      row('parent', at(1000), 3000),
      row('llm', at(1000), 2000, 1),
      row('tool', at(3000), 1000, 1),
    ]);
    assert.equal(timeline.totalMs, 3000);
    assert.equal(timeline.items[2].endMs, 3000);
    assert.equal(timeline.items[2].leftPercent, (2000 / 3000) * 100);
    assert.equal(timeline.items[2].widthPercent, (1000 / 3000) * 100);
  });

  it('keeps concurrent measured siblings and does not force a contiguous sequence', () => {
    const timeline = buildTraceTimeline([
      row('root', at(0), 1000),
      row('one', at(100), 600, 1),
      row('two', at(100), 400, 1),
    ]);
    assert.equal(timeline.items[1].startMs, 100);
    assert.equal(timeline.items[2].startMs, 100);
    assert.equal(timeline.totalMs, 1000);
  });

  it('places missing timestamps sequentially per parent and sibling, including subtrees', () => {
    const timeline = buildTraceTimeline([
      row('root', null, 500),
      row('first', null, 100, 1),
      row('grandchild', null, 25, 2),
      row('second', null, 200, 1),
      row('next-root', null, 50),
    ]);
    assert.deepEqual(timeline.items.map(({ startMs }) => startMs), [0, 0, 0, 100, 500]);
    assert.equal(timeline.totalMs, 550);
    assert.equal(timeline.hasEstimatedTiming, true);
    assert.deepEqual(timeline.items[2].ancestorBranches, [true]);
    assert.equal(timeline.items[1].isLastSibling, false);
    assert.equal(timeline.items[3].isLastSibling, true);
  });

  it('anchors untimed ancestors to measured descendants and derives unknown parent duration', () => {
    const timeline = buildTraceTimeline([
      row('unrelated', at(1000), 100),
      row('parent', null, null),
      row('child-one', at(1400), 50, 1),
      row('child-two', at(1600), 100, 1),
    ]);
    assert.equal(timeline.items[1].startMs, 400);
    assert.equal(timeline.items[1].durationMs, 300);
    assert.equal(timeline.items[1].estimatedDuration, true);
    assert.equal(timeline.items[2].estimatedStart, false);
    assert.equal(timeline.totalMs, 700);
  });

  it('treats null and invalid timestamps as missing rather than the Unix epoch', () => {
    const timeline = buildTraceTimeline([
      row('real', at(2000), 40),
      row('missing', null, 20),
      row('invalid', 'invalid date', 10),
    ]);
    assert.deepEqual(timeline.items.map(({ startMs }) => startMs), [0, 40, 60]);
    assert.equal(timeline.totalMs, 70);
  });

  it('retains a valid Unix-epoch timestamp and out-of-order recorded starts', () => {
    const timeline = buildTraceTimeline([row('later', at(500), 20), row('epoch', at(0), 10)]);
    assert.deepEqual(timeline.items.map(({ startMs }) => startMs), [500, 0]);
    assert.equal(timeline.totalMs, 520);
  });

  it('keeps zero and nonfinite timing geometry finite and fully within the track', () => {
    const timeline = buildTraceTimeline([
      row('zero', null, 0), row('nonfinite', null, Infinity), row('invalid', null, NaN), row('negative', null, -1),
    ]);
    assert.equal(timeline.totalMs, 0);
    assert.deepEqual(timeline.ticks, [{ fraction: 0, value: 0 }]);
    assert.equal(timeline.items[0].estimatedDuration, false);
    assert.equal(timeline.items[1].estimatedDuration, true);
    for (const item of [...timeline.items, barGeometry(Infinity, NaN, 0), barGeometry(1000, 0, 1000), barGeometry(999.999, .001, 1000)]) {
      assert.ok(Number.isFinite(item.leftPercent));
      assert.ok(Number.isFinite(item.widthPercent));
      assert.ok(item.leftPercent >= 0);
      assert.ok(item.widthPercent > 0);
      assert.ok(item.leftPercent + item.widthPercent <= 100);
    }
  });

  it('places an untimed sibling after the completion of an overlapping previous subtree', () => {
    const timeline = buildTraceTimeline([
      row('parent', at(0), 100),
      row('long-child', at(50), 200, 1),
      row('unknown-root', null, 20),
    ]);
    assert.equal(timeline.items[0].durationMs, 100);
    assert.equal(timeline.items[2].startMs, 250);
    assert.equal(timeline.totalMs, 270);
  });

  it('handles no rows without inventing elapsed time', () => {
    assert.equal(buildTraceTimeline().totalMs, 0);
    assert.deepEqual(buildTraceTimeline().items, []);
  });
});
