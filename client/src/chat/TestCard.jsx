import { useState } from 'react';

export default function TestCard({ card, onRun, onViewEvidence, onFindFix, findingFix }) {
  const [showContext, setShowContext] = useState(false);
  const [context, setContext] = useState(card.context ?? '');

  if (card.status === 'done' && card.result) {
    const r = card.result;
    const failed = r.verdict === 'fail';
    return (
      <div className="card test-card">
        <p className="result-head">
          {failed ? 'Failed' : 'Passed'}
          {r.rule ? ` · ${r.rule}` : ''}
        </p>
        <p>Expected: {r.expected}</p>
        <p className={failed ? 'actual-bad' : 'actual-good'}>Agent said: {r.actual}</p>
        {r.explanation ? <p className="muted">{r.explanation}</p> : null}
        <p>
          <button type="button" className="link" onClick={onViewEvidence}>
            View evidence in Dashboard →
          </button>
        </p>
        {r.resultId ? (
          <button
            type="button"
            className="btn-primary"
            disabled={findingFix}
            onClick={onFindFix}
          >
            {findingFix ? 'Starting…' : 'Find a fix'}
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="card test-card">
      <h3>{card.title}</h3>
      <p className="muted">Expected: {card.expected}</p>
      {card.status === 'running' ? (
        <p className="muted" role="status">
          {card.progress ?? 'Running test…'}
        </p>
      ) : (
        <>
          {showContext ? (
            <textarea
              value={context}
              rows={3}
              placeholder="Paste policy or extra context…"
              aria-label="Policy or context"
              onChange={(e) => setContext(e.target.value)}
            />
          ) : (
            <button type="button" className="link" onClick={() => setShowContext(true)}>
              + Add policy or context
            </button>
          )}
          <div>
            <button type="button" className="btn-primary" onClick={() => onRun(card.id, context)}>
              Run test
            </button>
          </div>
        </>
      )}
    </div>
  );
}
