import { useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import { useFetch } from '../hooks.js';
import { Empty, ErrorBox, Modal, Spinner, fmtPct, fmtTime, shortId } from '../ui.jsx';

const TYPE_LABELS = {
  llm: 'LLM',
  retrieval: 'Retrieval',
  tool: 'Tool',
  validation: 'Validation',
  router: 'Router',
  agent: 'Agent',
};

function spanLabel(node) {
  const label = TYPE_LABELS[node.type] ?? node.type;
  if (node.type === 'agent' && node.payload?.name) return `${label} · ${node.payload.name}`;
  if (node.type === 'router' && node.payload?.choice) return `${label} → ${node.payload.choice}`;
  return label;
}

function formatDuration(ms) {
  if (!Number.isFinite(ms)) return '—';
  const seconds = ms >= 1000;
  const value = seconds ? ms / 1000 : ms;
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${seconds ? 's' : 'ms'}`;
}

function flattenTree(roots) {
  const out = [];
  (function walk(nodes, depth) {
    for (const node of nodes ?? []) {
      out.push({ node, depth });
      walk(node.children, depth + 1);
    }
  })(roots, 0);
  return out;
}

function Waterfall({ rows, selectedId, onSelect }) {
  const times = rows
    .map((r) => new Date(r.node.started_at).getTime())
    .filter((t) => !Number.isNaN(t));
  const base = times.length ? Math.min(...times) : 0;
  const ends = rows.map((r, i) => {
    const start = times.length ? (new Date(r.node.started_at).getTime() || base) - base : i * 100;
    return start + (r.node.latency_ms ?? 0);
  });
  const total = Math.max(1, ...ends);
  const ticks = [0, 0.5, 1].map((f) => Math.round(total * f));

  return (
    <div className="waterfall-wrap scroll-x">
      <div className="waterfall-axis">
        <span />
        <span className="axis-row">
          {ticks.map((t, i) => (
            <span key={i}>
              {formatDuration(t)}
            </span>
          ))}
        </span>
      </div>
      <ul className="waterfall">
        {rows.map((r, i) => {
          const start = times.length ? (new Date(r.node.started_at).getTime() || base) - base : i * 100;
          const width = Math.max(2, ((r.node.latency_ms ?? 0) / total) * 100);
          return (
            <li key={r.node.id}>
              <button
                type="button"
                className={r.node.id === selectedId ? 'selected' : ''}
                onClick={() => onSelect(r.node.id)}
              >
                <span className="span-name" style={{ paddingLeft: r.depth * 16 }}>
                  {spanLabel(r.node)}
                  <span className="muted"> · {r.node.sdk_span_id}</span>
                </span>
                <span className="span-bar-track">
                  <span
                    className="span-bar"
                    style={{ left: `${(start / total) * 100}%`, width: `${width}%` }}
                  />
                </span>
                <span className="muted">{formatDuration(r.node.latency_ms)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function TestCaseModal({ testCase, onClose, onInspectInRuns, onTestInNewChat }) {
  const [assertions, setAssertions] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!testCase?.id) return;
    setLoading(true);
    api(`/v1/test-cases/${testCase.id}/assertions`)
      .then((data) => setAssertions(Array.isArray(data) ? data : (data.items ?? [])))
      .catch(() => setAssertions([]))
      .finally(() => setLoading(false));
  }, [testCase?.id]);

  if (!testCase) return null;

  return (
    <Modal title={`Test case ${shortId(testCase.id)}`} onClose={onClose}>
      <div className="test-modal-content">
        <div className="section-head">
          <span className="muted">
            Origin: <strong>{testCase.origin ?? 'from_trace'}</strong>
            {testCase.source_trace_id ? (
              <span> · Source trace <code>tr_{shortId(testCase.source_trace_id)}</code></span>
            ) : null}
          </span>
          <span className={`verdict ${testCase.split === 'holdout' ? 'neutral' : 'good'}`}>
            {testCase.split ?? 'dev'} split
          </span>
        </div>

        <p className="metric-label">Input</p>
        <div className="card" style={{ padding: '12px', margin: '4px 0 12px 0' }}>
          <p style={{ margin: 0 }}>{testCase.input || '—'}</p>
        </div>

        <p className="metric-label">Expected</p>
        <div className="card" style={{ padding: '12px', margin: '4px 0 12px 0' }}>
          {testCase.expected && Object.keys(testCase.expected).length > 0 ? (
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {JSON.stringify(testCase.expected, null, 2)}
            </pre>
          ) : (
            <p className="muted" style={{ margin: 0 }}>
              No custom expected output configured (preserves original input for regression testing).
            </p>
          )}
        </div>

        <p className="metric-label">Assertions ({assertions?.length ?? 0})</p>
        {loading ? (
          <Spinner label="Loading assertions…" />
        ) : assertions?.length ? (
          <ul style={{ margin: '4px 0 16px 0', paddingLeft: '20px' }}>
            {assertions.map((a, i) => (
              <li key={a.id ?? i} className="muted">
                <strong>{a.type}</strong>: {JSON.stringify(a.spec ?? {})}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted" style={{ margin: '4px 0 16px 0' }}>
            No custom assertions attached. Tests are evaluated against determinism and LLM judges.
          </p>
        )}

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
          {onTestInNewChat ? (
            <button
              type="button"
              className="btn-primary"
              onClick={() => {
                onClose();
                onTestInNewChat(testCase.id);
              }}
            >
              Test in new chat →
            </button>
          ) : null}
          {onInspectInRuns ? (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                onClose();
                onInspectInRuns(testCase);
              }}
            >
              Inspect in Runs →
            </button>
          ) : null}
        </div>
      </div>
    </Modal>
  );
}

export default function TracesTab({
  projectId,
  versions,
  labels,
  dash,
  patchDash,
  chatCredentialId,
  onOpenReview,
  showToast,
  onTestInNewChat,
}) {
  const [notice, setNotice] = useState('');
  const [working, setWorking] = useState('');
  const [viewingCase, setViewingCase] = useState(null);
  const [latestCreatedTest, setLatestCreatedTest] = useState(null);

  const versionFilter = dash.traceVersion ?? 'all';
  const statusFilter = dash.traceStatus ?? 'all';

  const versionSig = versions.map((v) => v.version_id).join(',');
  const list = useFetch(`traces-${projectId}-${versionFilter}-${statusFilter}-${versionSig}`, async () => {
    const params = new URLSearchParams({ limit: '100' });
    if (versionFilter !== 'all') params.set('version_id', versionFilter);
    if (statusFilter !== 'all') params.set('status', statusFilter);
    const page = await api(`/v1/traces?${params}`);
    const ids = new Set(versions.map((v) => v.version_id));
    return (page.items ?? []).filter((t) => versionFilter !== 'all' || ids.has(t.app_version_id));
  });

  const traceId = dash.traceId ?? list.data?.[0]?.id ?? null;
  const detail = useFetch(
    traceId ? `trace-${traceId}` : null,
    () => api(`/v1/traces/${traceId}`),
  );

  const testCases = useFetch(
    projectId ? `cases-${projectId}` : null,
    () => api(`/v1/projects/${projectId}/test-cases?limit=200`),
  );

  const traceDiagnoses = useFetch(
    traceId ? `diagnoses-${traceId}` : null,
    () => api(`/v1/diagnoses?trace_id=${traceId}`),
  );

  const existingCase = useMemo(() => {
    if (!traceId) return null;
    return (testCases.data?.items ?? []).find((c) => c.source_trace_id === traceId) ?? null;
  }, [testCases.data, traceId]);

  const activeCase = latestCreatedTest?.source_trace_id === traceId ? latestCreatedTest : existingCase;

  const existingDiagnosis = useMemo(() => {
    return (traceDiagnoses.data?.items ?? [])[0] ?? null;
  }, [traceDiagnoses.data]);

  const rows = useMemo(() => flattenTree(detail.data?.span_tree ?? []), [detail.data]);
  const spanId = dash.spanId ?? rows[0]?.node.id ?? null;
  const span = rows.find((r) => r.node.id === spanId)?.node ?? null;

  async function createTest() {
    if (!traceId || working) return;
    if (activeCase) {
      setViewingCase(activeCase);
      setNotice(`Created test ${shortId(activeCase.id)}.`);
      showToast?.({
        title: 'Test ready',
        message: 'Created test',
        testId: activeCase.id,
        actionLabel: 'Test in new chat →',
        onAction: () => onTestInNewChat?.(activeCase.id, projectId),
      });
      return;
    }
    setWorking('test');
    setNotice('');
    try {
      const created = await api(`/v1/projects/${projectId}/test-cases/from-trace/${traceId}`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      setLatestCreatedTest(created);
      testCases.reload();
      setNotice(`Created test ${shortId(created.id)}.`);
      showToast?.({
        title: 'Test created',
        message: 'Created test',
        testId: created.id,
        actionLabel: 'Test in new chat →',
        onAction: () => onTestInNewChat?.(created.id, projectId),
      });
    } catch (e) {
      setNotice(e?.message ?? 'Could not create a test from this trace.');
    } finally {
      setWorking('');
    }
  }

  async function diagnose() {
    if (!traceId || working) return;
    setWorking('diagnose');
    setNotice('');
    try {
      const body = chatCredentialId ? { credential_id: chatCredentialId } : {};
      const diagnosis = await api(`/v1/traces/${traceId}/diagnose`, {
        method: 'POST',
        body: JSON.stringify(body),
      });

      traceDiagnoses.reload();

      // Ensure a regression test case exists for repair engine
      let targetCase = activeCase;
      if (!targetCase) {
        try {
          targetCase = await api(`/v1/projects/${projectId}/test-cases/from-trace/${traceId}`, {
            method: 'POST',
            body: JSON.stringify({}),
          });
          setLatestCreatedTest(targetCase);
          testCases.reload();
        } catch {
          // It's ok if creating target test fails; diagnosis is still recorded
        }
      }

      let repairId = null;
      let repairError = null;
      if (chatCredentialId && targetCase) {
        try {
          const repair = await api(`/v1/diagnoses/${diagnosis.id}/repairs`, {
            method: 'POST',
            body: JSON.stringify(body),
          });
          repairId = repair.id;
        } catch (err) {
          repairError = err?.message ?? 'Could not propose a repair.';
        }
      } else if (!chatCredentialId) {
        repairError = 'No credential configured to generate automated repairs.';
      }

      onOpenReview({
        diagnosisId: diagnosis.id,
        repairId,
        repairError,
      });
    } catch (e) {
      setNotice(e?.message ?? 'Could not diagnose this trace.');
    } finally {
      setWorking('');
    }
  }

  return (
    <div className="traces">
      <h2>What happened inside the request?</h2>
      <p className="muted">Execution evidence: requests, span trees, retrieved context, and payloads.</p>
      <div className="runs-grid">
        <aside className="rail" aria-label="Requests">
          <h3>Requests</h3>
          <div className="rail-filters">
            <select
              aria-label="Filter by version"
              value={versionFilter}
              onChange={(e) => patchDash({ traceVersion: e.target.value, traceId: null, spanId: null })}
            >
              <option value="all">All versions</option>
              {versions.map((v) => (
                <option key={v.version_id} value={v.version_id}>
                  {v.label}
                </option>
              ))}
            </select>
            <select
              aria-label="Filter by execution"
              value={statusFilter}
              onChange={(e) => patchDash({ traceStatus: e.target.value, traceId: null, spanId: null })}
            >
              <option value="all">Execution: all</option>
              <option value="ok">Execution: ok</option>
              <option value="error">Execution: error</option>
            </select>
          </div>
          {list.loading ? (
            <Spinner />
          ) : list.error ? (
            <ErrorBox error={list.error} onRetry={list.reload} />
          ) : !list.data?.length ? (
            <p className="muted">No requests match.</p>
          ) : (
            <ul>
              {list.data.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    className={t.id === traceId ? 'selected' : ''}
                    onClick={() => patchDash({ traceId: t.id, spanId: null, traceVerdict: null })}
                  >
                    <strong className="truncate">
                      {t.input ? t.input.slice(0, 48) : `tr_${shortId(t.id)}`}
                    </strong>
                    <span className="muted">
                      tr_{shortId(t.id)} · {labels[t.app_version_id] ?? ''} · {fmtTime(t.created_at)}
                    </span>
                    <span className={t.status === 'ok' ? 'good-text' : 'bad-text'}>
                      Execution {t.status === 'ok' ? 'ok' : 'error'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {traceId ? (
            <div className="rail-actions">
              <button type="button" className="link" onClick={createTest} disabled={!!working}>
                {working === 'test' ? 'Creating test…' : 'Create test from trace →'}
              </button>
              {activeCase ? (
                <p className="muted" style={{ margin: '4px 0 0 0', fontSize: '0.85rem' }}>
                  Created test{' '}
                  <button
                    type="button"
                    className="link test-id-link"
                    onClick={() => setViewingCase(activeCase)}
                    title="Click to view test details"
                  >
                    {shortId(activeCase.id)}
                  </button>
                  .
                </p>
              ) : null}
            </div>
          ) : null}
        </aside>
        <div className="runs-main">
          {!traceId ? (
            <Empty title="No request selected" hint="Select a request from the rail." />
          ) : detail.loading ? (
            <Spinner />
          ) : detail.error ? (
            <ErrorBox error={detail.error} onRetry={detail.reload} />
          ) : (
            <>
              <div className="section-head">
                <h3>tr_{shortId(traceId)}</h3>
                <span className="muted">
                  Execution {detail.data.status === 'ok' ? 'ok' : 'error'}
                  {dash.traceVerdict !== null && dash.traceVerdict !== undefined
                    ? ` · Test ${dash.traceVerdict ? 'passed' : 'failed'}`
                    : ''}
                </span>
              </div>
              <p className="metric-label">Input</p>
              <p>{DetailIO(detail.data.input)}</p>
              <p className="metric-label">Output</p>
              <p className={detail.data.status === 'ok' ? '' : 'bad-text'}>
                {DetailIO(detail.data.output)}
              </p>
              <h3>Span tree &amp; timing</h3>
              {rows.length ? (
                <Waterfall
                  rows={rows}
                  selectedId={spanId}
                  onSelect={(id) => patchDash({ spanId: id })}
                />
              ) : (
                <p className="muted">This request has no spans.</p>
              )}
              {span ? (
                <section aria-label="Span details">
                  <div className="section-head">
                    <h3>
                      {spanLabel(span)}
                      {(span.chunks?.length ?? 0) > 0 ? ` · ${span.chunks.length} chunks` : ''}
                    </h3>
                    <span className="muted">
                      {formatDuration(span.latency_ms)} · {span.status}
                    </span>
                  </div>
                  <details>
                    <summary>Payload {JSON.stringify(span.payload ?? {}).slice(0, 40)}…</summary>
                    <pre>{JSON.stringify(span.payload ?? {}, null, 2)}</pre>
                  </details>
                  {(span.chunks ?? []).map((chunk, i) => (
                    <div key={chunk.id ?? i} className="chunk">
                      <p className="good-text">
                        Rank {chunk.rank} · {chunk.source ?? 'unknown'} · score {chunk.score ?? '—'}
                      </p>
                      <details open={i === 0}>
                        <summary>{String(chunk.text ?? '').slice(0, 80)}…</summary>
                        <p>{chunk.text}</p>
                      </details>
                    </div>
                  ))}
                </section>
              ) : null}
              <div className="trace-action-footer">
                <p>
                  <button type="button" className="link" onClick={diagnose} disabled={!!working}>
                    {working === 'diagnose' ? 'Diagnosing trace…' : 'Diagnose trace →'}
                  </button>
                </p>
                {activeCase ? (
                  <p className="muted">
                    Created test{' '}
                    <button
                      type="button"
                      className="link test-id-link"
                      onClick={() => setViewingCase(activeCase)}
                      title="Click to view test details"
                    >
                      {shortId(activeCase.id)}
                    </button>
                    .
                  </p>
                ) : notice ? (
                  <p className="muted">{notice}</p>
                ) : null}

                {existingDiagnosis ? (
                  <div className="card trace-diagnosis-card">
                    <div className="section-head">
                      <strong>Diagnosed: {existingDiagnosis.rule}</strong>
                      <span className="muted">
                        {existingDiagnosis.confidence !== null && existingDiagnosis.confidence !== undefined
                          ? `${fmtPct(existingDiagnosis.confidence)} confidence`
                          : ''}
                      </span>
                    </div>
                    {existingDiagnosis.evidence?.rationale ? (
                      <p className="muted" style={{ margin: '6px 0' }}>
                        {existingDiagnosis.evidence.rationale}
                      </p>
                    ) : null}
                    <button
                      type="button"
                      className="link"
                      onClick={() => onOpenReview({ diagnosisId: existingDiagnosis.id })}
                    >
                      View diagnosis in Improvements →
                    </button>
                  </div>
                ) : null}
              </div>
            </>
          )}
        </div>
      </div>
      {viewingCase ? (
        <TestCaseModal
          testCase={viewingCase}
          onClose={() => setViewingCase(null)}
          onTestInNewChat={(id) => onTestInNewChat?.(id, projectId)}
          onInspectInRuns={(c) => {
            patchDash({ tab: 'runs', splitFilter: c.split ?? 'all' });
          }}
        />
      ) : null}
    </div>
  );
}

function DetailIO(value) {
  return value === null || value === undefined || value === '' ? '—' : String(value);
}
