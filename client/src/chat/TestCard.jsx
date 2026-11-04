import { useState } from 'react';
import { ArrowUpRight, X } from 'lucide-react';
import ResponsePreview from './ResponsePreview.jsx';
import { Spinner, Verdict } from '../ui.jsx';

export default function TestCard({ card, onRun, onViewEvidence, onFindFix, findingFix, onRemove, disabled = false }) {
  const [showContext, setShowContext] = useState(false);
  const [context, setContext] = useState(card.context ?? '');

  if (card.status === 'done' && card.result) {
    const r = card.result;
    const failed = r.verdict === 'fail';
    const hasEvidence = Boolean(r.runId || r.resultId);
    const evidenceAction = onViewEvidence ? (
      <button
        type="button"
        className="evidence-action"
        onClick={hasEvidence ? onViewEvidence : undefined}
        disabled={!hasEvidence}
        title={hasEvidence ? 'Open the evidence for this test' : 'Evidence is unavailable for this result'}
      >
        View evidence in Dashboard
        <ArrowUpRight size={15} aria-hidden="true" />
      </button>
    ) : null;
    return (
      <div className="card test-card">
        <div className="test-result-head">
          <p className="result-head">
            <Verdict value={failed ? 'Failed' : 'Passed'} />
            {r.rule ? ` · ${r.rule}` : ''}
          </p>
          {evidenceAction}
        </div>
        <p>Expected: {r.expected}</p>
        <p className="response-caption">Agent response</p>
        <ResponsePreview action={evidenceAction}>{r.actual}</ResponsePreview>
        {r.explanation ? <ResponsePreview className="muted" title="Full test explanation" action={evidenceAction}>{r.explanation}</ResponsePreview> : null}
        {failed && r.resultId ? (
          <button
            type="button"
            className="btn-primary"
            disabled={findingFix || disabled}
            onClick={onFindFix}
          >
            {findingFix ? <Spinner label="Starting…" /> : 'Find a fix'}
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="card test-card">
      <div className="test-card-head">
        <h3>{card.title}</h3>
        {onRemove && card.status === 'ready' ? (
          <button type="button" className="btn-ghost remove-suggestion" onClick={onRemove} disabled={disabled} aria-label={`Remove ${card.title}`} title="Remove suggestion"><X size={16} aria-hidden="true" /></button>
        ) : null}
      </div>
      <p className="muted">Expected: {card.expected}</p>
      {card.status === 'running' ? (
        <Spinner label={card.progress ?? 'Running test…'} />
      ) : (
        <>
          {showContext ? (
            <textarea
              value={context}
              rows={3}
              placeholder="Paste policy or extra context…"
              aria-label="Policy or context"
              disabled={disabled}
              onChange={(e) => setContext(e.target.value)}
            />
          ) : (
            <button type="button" className="link" disabled={disabled} onClick={() => setShowContext(true)}>
              + Add policy or context
            </button>
          )}
          <div>
            <button type="button" className="btn-primary" disabled={disabled} onClick={() => onRun(card.id, context)}>
              Run test
            </button>
          </div>
        </>
      )}
    </div>
  );
}
