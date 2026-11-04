import { useMemo } from 'react';
import { buildTraceTimeline, formatTraceDuration, spanLabel } from './traceTimeline.js';
import './traceWaterfall.css';

export default function TraceWaterfall({ rows, selectedId, onSelect }) {
  const timeline = useMemo(() => buildTraceTimeline(rows), [rows]);
  return (
    <section className="trace-timeline" aria-label="Span timeline">
      <div className="trace-timeline-scroll" tabIndex={0} aria-label="Scrollable span timing chart">
        <div className="trace-timeline-grid">
          <div className="trace-timeline-axis trace-timeline-columns" aria-hidden="true">
            <span>Span</span>
            <span className="trace-tick-track">
              {timeline.ticks.map((tick) => (
                <span key={tick.fraction} className={`trace-tick${tick.fraction === 0 ? ' first' : tick.fraction === 1 ? ' last' : ''}`} style={{ left: `${tick.fraction * 100}%` }}>
                  {formatTraceDuration(tick.value)}
                </span>
              ))}
            </span>
            <span className="trace-duration">Duration</span>
          </div>
          <ul className="trace-timeline-list" aria-label="Trace spans">
            {timeline.items.map((item, index) => {
              const { node } = item;
              const label = spanLabel(node);
              const estimated = item.estimatedStart || item.estimatedDuration;
              const startText = `${formatTraceDuration(item.startMs)}${item.estimatedStart ? ' (estimated)' : ''}`;
              const durationText = `${formatTraceDuration(item.durationMs)}${item.estimatedDuration ? ' (estimated)' : ''}`;
              const description = `${label}${node.sdk_span_id ? ` · ${node.sdk_span_id}` : ''}, start +${startText}, duration ${durationText}, ${node.status ?? 'unknown status'}`;
              return (
                <li key={node.id ?? `${node.sdk_span_id ?? 'span'}-${index}`}>
                  <button
                    type="button"
                    className={`trace-span-row trace-timeline-columns${node.id === selectedId ? ' selected' : ''}`}
                    aria-pressed={node.id === selectedId}
                    aria-label={description}
                    title={description}
                    onClick={() => onSelect?.(node.id)}
                  >
                    <span className="trace-span-label" style={{ paddingLeft: `${24 + item.depth * 16}px` }}>
                      {item.ancestorBranches.map((continued, level) => continued ? (
                        <span key={level} className="trace-tree-line ancestor" style={{ left: `${8 + level * 16}px` }} aria-hidden="true" />
                      ) : null)}
                      {item.depth > 0 ? (
                        <span className={`trace-tree-branch${item.isLastSibling ? ' last' : ''}`} style={{ left: `${8 + (item.depth - 1) * 16}px` }} aria-hidden="true" />
                      ) : null}
                      {item.hasChildren ? <span className="trace-tree-line children" style={{ left: `${8 + item.depth * 16}px` }} aria-hidden="true" /> : null}
                      <span className="trace-tree-dot" style={{ left: `${5 + item.depth * 16}px` }} aria-hidden="true" />
                      <span className="trace-span-name">
                        <span>{label}</span>
                        {node.sdk_span_id ? <span className="trace-span-id">{node.sdk_span_id}</span> : null}
                      </span>
                    </span>
                    <span className="trace-span-track" aria-hidden="true">
                      <span
                        className={`trace-timing-bar trace-type-${node.type}${estimated ? ' estimated' : ''}${node.status === 'error' ? ' error' : ''}`}
                        style={{ left: `${item.leftPercent}%`, width: `${item.widthPercent}%` }}
                      />
                    </span>
                    <span className="trace-duration">{item.estimatedDuration ? '≈ ' : ''}{formatTraceDuration(item.durationMs)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
      <p className="trace-timeline-note">
        Shared elapsed time: {formatTraceDuration(timeline.totalMs)}.
        {timeline.hasEstimatedTiming ? ' Dashed bars indicate estimated timing where timestamps or durations are missing.' : ''}
      </p>
    </section>
  );
}
