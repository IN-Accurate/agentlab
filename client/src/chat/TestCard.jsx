import { useState } from 'react';
import { ArrowUpRight, X } from 'lucide-react';
import ResponsePreview from './ResponsePreview.jsx';
import { Spinner, Verdict } from '../ui.jsx';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

export default function TestCard({ card, onRun, onViewEvidence, onFindFix, findingFix, onRemove, disabled = false }) {
  const [showContext, setShowContext] = useState(Boolean(card.context));
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
          <Button
            type="button"
            variant="default"
            disabled={findingFix || disabled}
            onClick={onFindFix}
          >
            {findingFix ? <Spinner label="Starting…" /> : 'Find a fix'}
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="card test-card">
      <div className="test-card-head">
        <h3>{card.title}</h3>
        {onRemove && card.status === 'ready' ? (
          <Button type="button" variant="ghost" size="icon-sm" className="remove-suggestion" onClick={onRemove} disabled={disabled} aria-label={`Remove ${card.title}`} title="Remove suggestion"><X size={16} aria-hidden="true" /></Button>
        ) : null}
      </div>
      <p className="muted">Expected: {card.expected}</p>
      {card.status === 'running' ? (
        <Spinner label={card.progress ?? 'Running test…'} />
      ) : (
        <>
          {showContext ? (
            <Textarea
              value={context}
              rows={3}
              placeholder="Paste policy or extra context…"
              aria-label="Policy or context"
              disabled={disabled}
              onChange={(e) => setContext(e.target.value)}
            />
          ) : (
            <Button type="button" variant="inline" size="inline" disabled={disabled} onClick={() => setShowContext(true)}>
              + Add policy or context
            </Button>
          )}
          <div>
            <Button type="button" variant="default" disabled={disabled} onClick={() => onRun(card.id, context)}>
              Run test
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
