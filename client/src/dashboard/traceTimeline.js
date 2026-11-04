const TYPE_LABELS = {
  llm: 'LLM', retrieval: 'Retrieval', tool: 'Tool', validation: 'Validation', router: 'Router', agent: 'Agent',
};

export function spanLabel(node) {
  const label = TYPE_LABELS[node.type] ?? node.type ?? 'Span';
  if (node.type === 'agent' && node.payload?.name) return `${label} · ${node.payload.name}`;
  if (node.type === 'router' && node.payload?.choice) return `${label} → ${node.payload.choice}`;
  return label;
}

export function formatTraceDuration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const seconds = ms >= 1000;
  return `${(seconds ? ms / 1000 : ms).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${seconds ? 's' : 'ms'}`;
}

function timestamp(value) {
  // Date(null) and Date('') must not turn a missing timestamp into the epoch.
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null;
  const parsed = value instanceof Date ? value.getTime() : new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

function duration(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

export function barGeometry(startMs, durationMs, totalMs) {
  const scale = totalMs > 0 && Number.isFinite(totalMs) ? totalMs : 1;
  const start = Number.isFinite(startMs) ? Math.max(0, startMs) : 0;
  const length = Number.isFinite(durationMs) ? Math.max(0, durationMs) : 0;
  const trueStart = Math.min(100, Math.max(0, (start / scale) * 100));
  const trueWidth = Math.min(100, Math.max(0, (length / scale) * 100));
  // Subpixel and instantaneous spans remain visible, including at the right edge.
  // Their exact offset/duration stay available in the accessible label and title.
  const widthPercent = Math.min(100, Math.max(0.6, trueWidth));
  const leftPercent = Math.min(trueStart, 100 - widthPercent);
  return { leftPercent, widthPercent };
}

// Flattened input is in tree order. Real timestamps always win; only missing
// timing uses a sequential sibling fallback, which is explicitly marked inferred.
export function buildTraceTimeline(rows = []) {
  const records = [];
  const roots = [];
  const stack = [];
  let base = Infinity;
  for (const row of rows) {
    const requestedDepth = Number.isFinite(row.depth) ? Math.max(0, Math.floor(row.depth)) : 0;
    const depth = Math.min(requestedDepth, stack.length);
    stack.length = depth;
    const parent = stack[depth - 1] ?? null;
    const start = timestamp(row.node.started_at);
    const record = {
      node: row.node, depth, parent, children: [], timestamp: start,
      recordedDurationMs: duration(row.node.latency_ms), anchor: start ?? Infinity,
    };
    if (start !== null) base = Math.min(base, start);
    (parent?.children ?? roots).push(record);
    records.push(record);
    stack.push(record);
  }
  if (!Number.isFinite(base)) base = 0;
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record.parent) record.parent.anchor = Math.min(record.parent.anchor, record.anchor);
  }

  function resolveSiblings(siblings, parentStart) {
    let cursor = parentStart;
    for (let index = 0; index < siblings.length; index += 1) {
      const record = siblings[index];
      record.estimatedStart = record.timestamp === null;
      record.startMs = record.timestamp !== null
        ? record.timestamp - base
        : Number.isFinite(record.anchor) ? record.anchor - base : cursor;
      const childrenEnd = resolveSiblings(record.children, record.startMs);
      record.estimatedDuration = record.recordedDurationMs === null;
      record.durationMs = record.recordedDurationMs ?? Math.max(0, childrenEnd - record.startMs);
      record.endMs = record.startMs + record.durationMs;
      record.completionMs = Math.max(record.endMs, childrenEnd);
      record.isLastSibling = index === siblings.length - 1;
      cursor = Math.max(cursor, record.completionMs);
    }
    return cursor;
  }

  const totalMs = resolveSiblings(roots, 0);
  const items = records.map((record) => {
    const ancestors = [];
    for (let ancestor = record.parent; ancestor; ancestor = ancestor.parent) ancestors.unshift(ancestor);
    return {
      node: record.node,
      depth: record.depth,
      startMs: record.startMs,
      durationMs: record.durationMs,
      endMs: record.endMs,
      estimatedStart: record.estimatedStart,
      estimatedDuration: record.estimatedDuration,
      hasChildren: record.children.length > 0,
      isLastSibling: record.isLastSibling,
      // A vertical ancestor branch continues while that ancestor has a sibling.
      ancestorBranches: ancestors.slice(1).map((ancestor) => !ancestor.isLastSibling),
      ...barGeometry(record.startMs, record.durationMs, totalMs),
    };
  });
  return {
    items,
    totalMs,
    hasEstimatedTiming: items.some((item) => item.estimatedStart || item.estimatedDuration),
    ticks: (totalMs > 0 ? [0, 0.25, 0.5, 0.75, 1] : [0]).map((fraction) => ({ fraction, value: totalMs * fraction })),
  };
}
