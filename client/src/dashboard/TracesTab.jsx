import { useMemo, useState } from 'react';
import { api } from '../api.js';
import { useFetch } from '../hooks.js';
import { Empty, ErrorBox, Spinner, fmtTime, shortId } from '../ui.jsx';
import { useTraceRequests } from './useTraceRequests.js';
import './traceRequests.css';
import TraceWaterfall from './TraceWaterfall.jsx';
import { formatTraceDuration as formatDuration, spanLabel } from './traceTimeline.js';

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

export default function TracesTab({
  projectId,
  versions,
  labels,
  dash,
  patchDash,
  chatCredentialId,
  onOpenReview,
}) {
  const [notice, setNotice] = useState('');
  const [working, setWorking] = useState('');
  const versionFilter = dash.traceVersion ?? 'all';
  const statusFilter = dash.traceStatus ?? 'all';

  const list = useTraceRequests(projectId, versionFilter, statusFilter);

  const traceId = dash.traceId ?? list.items[0]?.id ?? null;
  const detail = useFetch(
    traceId ? `trace-${traceId}` : null,
    () => api(`/v1/traces/${traceId}`),
  );

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
    setWorking('test');
    setNotice('');
    try {
      const created = await api(`/v1/projects/${projectId}/test-cases/from-trace/${traceId}`, {
        method: 'POST',
      });
      setNotice(`Created test ${shortId(created.id)}.`);
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
      onOpenReview({ diagnosisId: diagnosis.id });
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
            <button
              type="button"
              className="btn-secondary"
              aria-label="Previous requests page"
              disabled={!list.hasPrevious || list.loading}
              onClick={() => turnPage('previous')}
            >
              Previous
            </button>
            <span className="request-page-number" aria-live="polite">Page {list.page}</span>
            <button
              type="button"
              className="btn-secondary"
              aria-label="Next requests page"
              disabled={!list.hasNext || list.loading || Boolean(list.error)}
              onClick={() => turnPage('next')}
            >
              Next
            </button>
          </nav>
          {traceId ? (
            <button type="button" className="link" onClick={createTest} disabled={!!working}>
              Create test from trace →
            </button>
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
              <p>
                <button type="button" className="link" onClick={diagnose} disabled={!!working}>
                  Diagnose trace →
                </button>
              </p>
              {notice ? <p className="muted">{notice}</p> : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function DetailIO(value) {
  return value === null || value === undefined || value === '' ? '—' : String(value);
}
