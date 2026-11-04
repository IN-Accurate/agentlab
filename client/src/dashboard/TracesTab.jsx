import { useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import { useFetch } from '../hooks.js';
import { Empty, ErrorBox, Modal, Spinner, fmtPct, fmtTime, shortId } from '../ui.jsx';
import { useTraceRequests } from './useTraceRequests.js';
import './traceRequests.css';
import TraceWaterfall from './TraceWaterfall.jsx';
import { formatTraceDuration as formatDuration, spanLabel } from './traceTimeline.js';
import { Button } from '@/components/ui/button';
import { NativeSelect } from '@/components/ui/native-select';

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
          <Button type="button" variant="outline" onClick={onClose}>
            Close
          </Button>
          {onTestInNewChat ? (
            <Button
              type="button"
              variant="default"
              onClick={() => {
                onClose();
                onTestInNewChat(testCase.id);
              }}
            >
              Test in new chat →
            </Button>
          ) : null}
          {onInspectInRuns ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                onClose();
                onInspectInRuns(testCase);
              }}
            >
              Inspect in Runs →
            </Button>
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

  const list = useTraceRequests(projectId, versionFilter, statusFilter);

  const traceId = dash.traceId ?? list.items[0]?.id ?? null;
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
  const spanId = rows.some((row) => row.node.id === dash.spanId)
    ? dash.spanId
    : rows[0]?.node.id ?? null;
  const span = rows.find((r) => r.node.id === spanId)?.node ?? null;

  function turnPage(direction) {
    list[direction]();
    patchDash({ traceId: null, spanId: null, traceVerdict: null });
  }

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
            <NativeSelect wrapperClassName="w-full"
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
            </NativeSelect>
            <NativeSelect wrapperClassName="w-full"
              aria-label="Filter by execution"
              value={statusFilter}
              onChange={(e) => patchDash({ traceStatus: e.target.value, traceId: null, spanId: null })}
            >
              <option value="all">Execution: all</option>
              <option value="ok">Execution: ok</option>
              <option value="error">Execution: error</option>
            </NativeSelect>
          </div>
          {list.loading ? (
            <Spinner />
          ) : list.error ? (
            <ErrorBox error={list.error} onRetry={list.reload} />
          ) : !list.items.length ? (
            <p className="muted">No requests match.</p>
          ) : (
            <ul>
              {list.items.map((t) => (
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
          <nav className="request-pagination" aria-label="Request pages">
            <Button
              type="button"
              variant="outline"
              aria-label="Previous requests page"
              disabled={!list.hasPrevious || list.loading}
              onClick={() => turnPage('previous')}
            >
              Previous
            </Button>
            <span className="request-page-number" aria-live="polite">Page {list.page}</span>
            <Button
              type="button"
              variant="outline"
              aria-label="Next requests page"
              disabled={!list.hasNext || list.loading || Boolean(list.error)}
              onClick={() => turnPage('next')}
            >
              Next
            </Button>
          </nav>
          {traceId ? (
            <div className="rail-actions">
              <Button type="button" variant="inline" size="inline" onClick={createTest} disabled={!!working}>
                {working === 'test' ? 'Creating test…' : 'Create test from trace →'}
              </Button>
              {activeCase ? (
                <p className="muted" style={{ margin: '4px 0 0 0', fontSize: '0.85rem' }}>
                  Created test{' '}
                  <Button
                    type="button"
                    variant="inline" size="inline" className="test-id-link"
                    onClick={() => setViewingCase(activeCase)}
                    title="Click to view test details"
                  >
                    {shortId(activeCase.id)}
                  </Button>
                  .
                </p>
              ) : null}
            </div>
          ) : null}
        </aside>
        <div className="runs-main">
          {list.loading && !traceId ? (
            <Spinner label="Loading requests…" />
          ) : !traceId ? (
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
                <TraceWaterfall
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
                  <Button type="button" variant="inline" size="inline" onClick={diagnose} disabled={!!working}>
                    {working === 'diagnose' ? 'Diagnosing trace…' : 'Diagnose trace →'}
                  </Button>
                </p>
                {activeCase ? (
                  <p className="muted">
                    Created test{' '}
                    <Button
                      type="button"
                      variant="inline" size="inline" className="test-id-link"
                      onClick={() => setViewingCase(activeCase)}
                      title="Click to view test details"
                    >
                      {shortId(activeCase.id)}
                    </Button>
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
                    <Button
                      type="button"
                      variant="inline" size="inline"
                      onClick={() => onOpenReview({ diagnosisId: existingDiagnosis.id })}
                    >
                      View diagnosis in Improvements →
                    </Button>
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
