import { useEffect, useMemo, useRef, useState } from 'react';
import { api, proposeFix } from '../api.js';
import { useFetch } from '../hooks.js';
import { fetchCache } from '../fetchCache.js';
import { Empty, ErrorBox, Spinner, Verdict, fmtTime, shortId } from '../ui.jsx';

async function fetchRail(projectId, versionIds, versionFilter, cursor) {
  const params = new URLSearchParams({ limit: '50' });
  if (versionFilter !== 'all') params.set('version_id', versionFilter);
  if (cursor) params.set('cursor', cursor);
  const page = await api(`/v1/runs?${params}`);
  const items = (page.items ?? []).filter(
    (r) => versionFilter !== 'all' || versionIds.has(r.app_version_id),
  );
  return { items, next: page.next_cursor ?? null };
}

function railState(key) {
  const cached = fetchCache.getCached(key);
  return {
    key,
    items: cached?.items ?? [],
    cursor: cached?.next ?? null,
    loading: cached === undefined,
    error: null,
  };
}

export function useRail(projectId, versions, versionFilter, nonce) {
  const versionIds = useMemo(() => new Set(versions.map((v) => v.version_id)), [versions]);
  const key = `run-rail-${projectId}-${versionFilter}-${[...versionIds].join(',')}-${nonce}`;
  const [state, setState] = useState(() => railState(key));
  const generation = useRef(0);
  const currentKey = useRef(key);
  const paging = useRef(false);
  currentKey.current = key;

  useEffect(() => {
    const id = ++generation.current;
    paging.current = false;
    setState(railState(key));
    fetchCache.fetchShared(key, () => fetchRail(projectId, versionIds, versionFilter, null)).then(
      ({ items, next }) => {
        if (id !== generation.current || currentKey.current !== key) return;
        setState({ key, items, cursor: next, loading: false, error: null });
      },
      (error) => {
        if (id !== generation.current || currentKey.current !== key) return;
        setState((s) => s.key === key ? { ...s, error, loading: false } : s);
      },
    );
    return () => {
      generation.current += 1;
    };
  }, [key, projectId, versionFilter, versionIds]);

  const current = state.key === key ? state : railState(key);

  async function more() {
    if (currentKey.current !== key || !current.cursor || current.loading || paging.current) return;
    const id = generation.current;
    paging.current = true;
    setState((s) => s.key === key ? { ...s, loading: true, error: null } : s);
    try {
      const { items, next } = await fetchRail(projectId, versionIds, versionFilter, current.cursor);
      if (id !== generation.current || currentKey.current !== key) return;
      setState((s) => s.key === key ? { ...s, items: [...s.items, ...items], cursor: next } : s);
    } catch (error) {
      if (id === generation.current && currentKey.current === key)
        setState((s) => s.key === key ? { ...s, error } : s);
    } finally {
      if (id === generation.current && currentKey.current === key) {
        paging.current = false;
        setState((s) => s.key === key ? { ...s, loading: false } : s);
      }
    }
  }

  return { ...current, more };
}

export default function RunsTab({
  projectId,
  versions,
  labels,
  dash,
  patchDash,
  chatCredentialId,
  onOpenTrace,
  onOpenReview,
  onContinueInChat,
}) {
  const versionFilter = dash.runVersion ?? 'all';
  const rail = useRail(projectId, versions, versionFilter, dash.runsNonce ?? 0);
  const [summaries, setSummaries] = useState({});
  const [diagnosing, setDiagnosing] = useState(false);
  const [actionError, setActionError] = useState('');

  const numbers = useMemo(() => {
    const ordered = [...rail.items].sort((a, b) =>
      String(a.created_at) < String(b.created_at) ? -1 : 1,
    );
    return Object.fromEntries(
      ordered.map((r, i) => [r.id, `R-${String(i + 1).padStart(3, '0')}`]),
    );
  }, [rail.items]);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled(
      rail.items.map((r) => fetchCache.fetchShared(
        `run-summary-${r.id}-${dash.runsNonce ?? 0}`,
        () => api(`/v1/runs/${r.id}/summary`),
      ).then((s) => [r.id, s])),
    ).then((done) => {
      if (cancelled) return;
      const next = {};
      for (const d of done) if (d.status === 'fulfilled') next[d.value[0]] = d.value[1];
      setSummaries((old) => ({ ...old, ...next }));
    });
    return () => {
      cancelled = true;
    };
  }, [rail.items, dash.runsNonce]);

  const runId = dash.runId ?? rail.items[0]?.id ?? null;
  const summary = useFetch(
    runId ? `run-summary-${runId}-${dash.runsNonce ?? 0}` : null,
    () => api(`/v1/runs/${runId}/summary`),
  );
  const cases = useFetch(
    projectId ? `cases-${projectId}` : null,
    () => api(`/v1/projects/${projectId}/test-cases?limit=200`),
  );
  const caseMap = useMemo(
    () => Object.fromEntries((cases.data?.items ?? []).map((t) => [t.id, t])),
    [cases.data],
  );

  const rows = useMemo(() => {
    const all = summary.data?.results ?? [];
    return all.filter((r) => {
      if ((dash.resultFilter ?? 'all') === 'failed' && r.passed) return false;
      if ((dash.resultFilter ?? 'all') === 'passed' && !r.passed) return false;
      if ((dash.splitFilter ?? 'all') !== 'all' && r.split !== dash.splitFilter) return false;
      return true;
    });
  }, [summary.data, dash.resultFilter, dash.splitFilter]);

  const failedCount = (summary.data?.results ?? []).filter((r) => !r.passed).length;
  const passedCount = (summary.data?.results ?? []).filter((r) => r.passed).length;

  async function diagnose(resultId) {
    if (!resultId || diagnosing) return;
    setDiagnosing(true);
    setActionError('');
    try {
      const diagnosis = await proposeFix(resultId, chatCredentialId);
      onOpenReview({ diagnosisId: diagnosis.id, repairError: diagnosis.repairError });
    } catch (e) {
      setActionError(e?.message ?? 'Could not diagnose this failure.');
    } finally {
      setDiagnosing(false);
    }
  }

  return (
    <div className="runs">
      <h2>Runs &amp; test results</h2>
      <p className="muted">One run evaluates selected test cases against one exact app version.</p>
      <div className="runs-grid">
        <aside className="rail" aria-label="Run history">
          <h3>Run history</h3>
          <select
            aria-label="Filter by version"
            value={versionFilter}
            onChange={(e) => patchDash({ runVersion: e.target.value, runId: null, resultId: null })}
          >
            <option value="all">All versions</option>
            {versions.map((v) => (
              <option key={v.version_id} value={v.version_id}>
                {v.label}
              </option>
            ))}
          </select>
          {rail.error ? <ErrorBox error={rail.error} /> : null}
          {rail.items.length === 0 && !rail.loading ? (
            <p className="muted">No runs yet.</p>
          ) : (
            <ul>
              {rail.items.map((r) => {
                const s = summaries[r.id];
                return (
                  <li key={r.id}>
                    <button
                      type="button"
                      className={r.id === runId ? 'selected' : ''}
                      onClick={() => patchDash({ runId: r.id, resultId: null })}
                    >
                      <span className="rail-top">
                        <strong>{numbers[r.id] ?? shortId(r.id)}</strong>
                        <span className="muted">{labels[r.app_version_id] ?? ''}</span>
                      </span>
                      <span className={s && s.failed === 0 ? 'good-text' : ''}>
                        {s ? `${s.passed} / ${s.total} passed` : '…'}
                      </span>
                      <span className="muted">{fmtTime(r.created_at)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {rail.cursor ? (
            <button type="button" className="link" onClick={rail.more} disabled={rail.loading}>
              Load more runs
            </button>
          ) : null}
        </aside>
        <div className="runs-main">
          {!runId ? (
            <Empty title="No run selected" hint="Select a run from the history rail." />
          ) : summary.loading ? (
            <Spinner />
          ) : summary.error ? (
            <ErrorBox error={summary.error} onRetry={summary.reload} />
          ) : (
            <>
              <div className="section-head">
                <h3>
                  {numbers[runId] ?? shortId(runId)} · {labels[summary.data.version_id] ?? ''}
                </h3>
                <CompareLink
                  runs={rail.items}
                  numbers={numbers}
                  dash={dash}
                  patchDash={patchDash}
                />
              </div>
              <p className="muted">
                {summary.data.passed} passed / {summary.data.total} · Dev{' '}
                {summary.data.dev.passed}/{summary.data.dev.total} · Holdout{' '}
                {summary.data.holdout.passed}/{summary.data.holdout.total}
              </p>
              <CompareView dash={dash} />
              <div className="filter-bar">
                <div role="group" aria-label="Result filter">
                  {[
                    ['all', 'All results'],
                    ['failed', `Failed (${failedCount})`],
                    ['passed', `Passed (${passedCount})`],
                  ].map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      className={(dash.resultFilter ?? 'all') === key ? 'active' : ''}
                      onClick={() => patchDash({ resultFilter: key })}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <select
                  aria-label="Filter by split"
                  value={dash.splitFilter ?? 'all'}
                  onChange={(e) => patchDash({ splitFilter: e.target.value })}
                >
                  <option value="all">All splits</option>
                  <option value="dev">Dev</option>
                  <option value="holdout">Holdout</option>
                </select>
              </div>
              <div className="scroll-x">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>Test input</th>
                      <th>Split</th>
                      <th>Verdict</th>
                      <th>Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id} className={dash.resultId === r.id ? 'selected' : ''}>
                        <td>
                          <button
                            type="button"
                            className="row-btn"
                            onClick={() => patchDash({ resultId: r.id })}
                          >
                            {caseMap[r.test_case_id]?.input ?? shortId(r.test_case_id)}
                          </button>
                        </td>
                        <td>{r.split}</td>
                        <td>
                          <Verdict value={r.passed} />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="link"
                            onClick={() => patchDash({ resultId: r.id })}
                          >
                            Inspect →
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {rows.length === 0 ? <p className="muted">No results match these filters.</p> : null}
              {dash.resultId ? (
                <Evidence
                  resultId={dash.resultId}
                  caseMap={caseMap}
                  onOpenTrace={onOpenTrace}
                  onDiagnose={() => diagnose(dash.resultId)}
                  diagnosing={diagnosing}
                  onContinueInChat={onContinueInChat}
                />
              ) : null}
              {actionError ? (
                <p className="field-error" role="alert">
                  {actionError}
                </p>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function CompareLink({ runs, numbers, dash, patchDash }) {
  const [open, setOpen] = useState(false);
  if (!open)
    return (
      <button type="button" className="link" onClick={() => setOpen(true)}>
        Compare runs →
      </button>
    );
  return (
    <span className="compare-pick">
      <select
        aria-label="Baseline run"
        value={dash.compareA ?? ''}
        onChange={(e) => patchDash({ compareA: e.target.value || null })}
      >
        <option value="">Baseline…</option>
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {numbers[r.id] ?? shortId(r.id)}
          </option>
        ))}
      </select>
      <select
        aria-label="Candidate run"
        value={dash.compareB ?? ''}
        onChange={(e) => patchDash({ compareB: e.target.value || null })}
      >
        <option value="">Candidate…</option>
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {numbers[r.id] ?? shortId(r.id)}
          </option>
        ))}
      </select>
    </span>
  );
}

function CompareView({ dash }) {
  const compared = useFetch(
    dash.compareA && dash.compareB ? `compare-${dash.compareA}-${dash.compareB}` : null,
    () => api(`/v1/runs/${dash.compareA}/compare/${dash.compareB}`),
  );
  if (!dash.compareA || !dash.compareB) return null;
  if (compared.loading) return <Spinner />;
  if (compared.error) return <ErrorBox error={compared.error} onRetry={compared.reload} />;
  const { baseline, candidate } = compared.data;
  return (
    <div className="compare-grid">
      {[
        ['Baseline', baseline],
        ['Candidate', candidate],
      ].map(([label, s]) => (
        <div key={label} className="card">
          <p className="metric-label">{label}</p>
          <p>
            {s.passed} / {s.total} passed
          </p>
          <p className="muted">
            Dev {s.dev.passed}/{s.dev.total} · Holdout {s.holdout.passed}/{s.holdout.total}
          </p>
        </div>
      ))}
    </div>
  );
}

function Evidence({ resultId, caseMap, onOpenTrace, onDiagnose, diagnosing, onContinueInChat }) {
  const detail = useFetch(`evidence-${resultId}`, () => api(`/v1/run-results/${resultId}`));
  const testCase = detail.data ? caseMap[detail.data.test_case_id] : null;
  const assertions = useFetch(
    detail.data ? `assertions-${detail.data.test_case_id}` : null,
    () => api(`/v1/test-cases/${detail.data.test_case_id}/assertions`),
  );
  const trace = useFetch(
    detail.data?.trace_id ? `evidence-trace-${detail.data.trace_id}` : null,
    () => api(`/v1/traces/${detail.data.trace_id}`),
  );
  if (detail.loading) return <Spinner />;
  if (detail.error) return <ErrorBox error={detail.error} onRetry={detail.reload} />;
  const rows = (detail.data.assertion_results ?? []).map((ar) => {
    const spec = (assertions.data?.items ?? []).find((a) => a.id === ar.assertion_id);
    return { ...ar, kind: spec?.kind ?? '—', label: spec?.spec?.label ?? spec?.spec?.name ?? '' };
  });
  const expected = testCase?.expected;
  return (
    <section aria-label="Result evidence" className="card-panel">
      <div className="section-head">
        <h3>Result evidence</h3>
        {detail.data.trace_id ? (
          <button
            type="button"
            className="link"
            onClick={() => onOpenTrace(detail.data.trace_id, detail.data.passed)}
          >
            Trace tr_{shortId(detail.data.trace_id)} →
          </button>
        ) : null}
      </div>
      <div className="evidence-io">
        <div>
          <p className="metric-label">Expected</p>
          <p>
            {expected
              ? (expected.expected_reply ??
                expected.behavior ??
                expected.reference ??
                JSON.stringify(expected))
              : '—'}
          </p>
        </div>
        <div>
          <p className="metric-label">Actual output</p>
          <p className={detail.data.passed ? '' : 'bad-text'}>
            {trace.data ? (trace.data.output ?? '—') : detail.data.trace_id ? '…' : '—'}
          </p>
        </div>
      </div>
      <ul className="assertion-list">
        {rows.map((ar) => (
          <li key={ar.id}>
            <strong>{ar.label || ar.kind}</strong>
            <span className="muted">{ar.kind}</span>
            <Verdict value={ar.passed === null ? 'Skipped' : ar.passed} />
            <span className="muted">
              {ar.evidence?.explanation ?? ar.evidence?.reason ?? 'Not evaluated'}
            </span>
          </li>
        ))}
      </ul>
      <div className="evidence-actions">
        {!detail.data.passed ? (
          <button type="button" className="btn-primary" disabled={diagnosing} onClick={onDiagnose}>
            {diagnosing ? 'Starting…' : 'Diagnose failure'}
          </button>
        ) : null}
        <button type="button" className="link" onClick={onContinueInChat}>
          Continue in chat →
        </button>
      </div>
    </section>
  );
}
