import { useMemo, useState } from 'react';
import { api } from '../api.js';
import { useFetch } from '../hooks.js';
import { Empty, ErrorBox, Spinner, fmtTime, shortId } from '../ui.jsx';

const TYPE_LABELS = { llm: 'LLM', retrieval: 'Retrieval', tool: 'Tool', validation: 'Validation' };

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
              {i === 0 ? '0 ms' : t.toLocaleString()}
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
                  {TYPE_LABELS[r.node.type] ?? r.node.type}
                  <span className="muted"> · {r.node.sdk_span_id}</span>
                </span>
                <span className="span-bar-track">
                  <span
                    className="span-bar"
                    style={{ left: `${(start / total) * 100}%`, width: `${width}%` }}
                  />
                </span>
                <span className="muted">{r.node.latency_ms ?? '—'} ms</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
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
}) {
  const [notice, setNotice] = useState('');
  const [working, setWorking] = useState('');
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

  const rows = useMemo(() => flattenTree(detail.data?.span_tree ?? []), [detail.data]);
  const spanId = dash.spanId ?? rows[0]?.node.id ?? null;
  const span = rows.find((r) => r.node.id === spanId)?.node ?? null;

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
            <button type="button" className="link" onClick={createTest} disabled={!!working}>
              Create test from trace →
            </button>
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
              <p>{detailIO(detail.data.input)}</p>
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
                      {TYPE_LABELS[span.type] ?? span.type}
                      {(span.chunks?.length ?? 0) > 0 ? ` · ${span.chunks.length} chunks` : ''}
                    </h3>
                    <span className="muted">
                      {span.latency_ms ?? '—'} ms · {span.status}
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
