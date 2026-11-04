import { useMemo } from 'react';
import { api } from '../api.js';
import { useFetch } from '../hooks.js';
import { Empty, ErrorBox, Spinner, fmtPct, shortId } from '../ui.jsx';

function TrendChart({ versions }) {
  const { points, ticks, domain } = useMemo(() => {
    const values = versions.map((v) => v.pass_rate).filter((v) => v !== null && v !== undefined);
    if (!values.length) return { points: [], ticks: [], domain: [0, 1] };
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const span = Math.max(hi - lo, 0.02);
    const d0 = Math.max(0, Math.floor((lo - span * 0.25) * 20) / 20);
    const d1 = Math.min(1, Math.ceil((hi + span * 0.25) * 20) / 20);
    const ticks = [0, 1, 2, 3, 4].map((i) => d0 + ((d1 - d0) * i) / 4);
    return { points: versions, ticks, domain: [d0, d1] };
  }, [versions]);

  const W = 900;
  const H = 260;
  const padL = 56;
  const padR = 16;
  const padT = 12;
  const padB = 40;
  const x = (i) =>
    points.length === 1
      ? padL + (W - padL - padR) / 2
      : padL + ((W - padL - padR) * i) / (points.length - 1);
  const y = (v) => padT + (H - padT - padB) * (1 - (v - domain[0]) / (domain[1] - domain[0] || 1));

  if (!points.length || !points.some((p) => p.pass_rate !== null && p.pass_rate !== undefined))
    return <p className="muted">No completed runs yet — pass rates will appear here.</p>;

  // Break the path at gaps: segments of consecutive values.
  const segments = [];
  let current = [];
  points.forEach((p, i) => {
    if (p.pass_rate === null || p.pass_rate === undefined) {
      if (current.length > 1) segments.push(current);
      current = [];
    } else current.push(i);
  });
  if (current.length > 1) segments.push(current);

  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Pass rate by version">
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className="grid" />
          <text x={padL - 8} y={y(t) + 4} textAnchor="end" className="axis">
            {Math.round(t * 100)}%
          </text>
        </g>
      ))}
      {segments.map((seg, i) => (
        <polyline
          key={i}
          className="trend-line"
          points={seg.map((j) => `${x(j).toFixed(1)},${y(points[j].pass_rate).toFixed(1)}`).join(' ')}
        />
      ))}
      {points.map((p, i) =>
        p.pass_rate === null || p.pass_rate === undefined ? null : (
          <circle key={p.version_id} cx={x(i)} cy={y(p.pass_rate)} r={5} className="trend-dot" />
        ),
      )}
      {points.map((p, i) => (
        <text key={p.version_id} x={x(i)} y={H - 8} textAnchor="middle" className="axis">
          {p.label} · {p.pass_rate === null || p.pass_rate === undefined ? 'n/a' : fmtPct(p.pass_rate)}
        </text>
      ))}
    </svg>
  );
}

async function latestChange(projectId) {
  const all = [];
  let cursor = null;
  for (;;) {
    const params = new URLSearchParams({ limit: '100', project_id: projectId });
    if (cursor) params.set('cursor', cursor);
    const page = await api(`/v1/repairs?${params}`);
    all.push(...(page.items ?? []));
    cursor = page.next_cursor ?? null;
    if (!cursor) break;
  }
  const items = [...all].sort((a, b) =>
    String(a.created_at) < String(b.created_at) ? 1 : -1,
  );
  for (const repair of items) {
    try {
      const scored = await api(`/v1/verifications?repair_id=${repair.id}&limit=50`);
      const verification = [...(scored.items ?? [])].sort((a, b) =>
        String(a.created_at) < String(b.created_at) ? 1 : -1,
      )[0];
      if (!verification) continue;
      const diagnosis = await api(`/v1/diagnoses/${repair.diagnosis_id}`);
      return { repair, diagnosis, verification };
    } catch {
      continue;
    }
  }
  return null;
}

export default function OverviewTab({ projectId, data, onReviewChange }) {
  const versions = data?.versions ?? [];
  const withSummary = versions.filter((v) => v.summary);
  const latest = withSummary[withSummary.length - 1] ?? null;
  const change = useFetch(`latest-change-${projectId}`, () => latestChange(projectId));

  const bars = useMemo(() => {
    const counts = {};
    for (const v of versions)
      for (const [rule, n] of Object.entries(v.failures_by_rule ?? {}))
        counts[rule] = (counts[rule] ?? 0) + n;
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [versions]);
  const barMax = Math.max(1, ...bars.map(([, n]) => n));

  if (!versions.length)
    return <Empty title="No versions yet" hint="Create a version to start tracking pass rates." />;

  return (
    <div className="overview">
      <h2>Is the agent getting better?</h2>
      <p className="muted">Latest saved results, compared across immutable versions.</p>
      <div className="metrics">
        <div className="card accent-card">
          <p className="metric-label">Latest pass rate</p>
          <p className={`metric-value ${latest ? 'good' : ''}`}>
            {latest ? fmtPct(latest.pass_rate, 0) : 'unavailable'}
          </p>
          <p className="muted">
            {latest
              ? `${latest.summary.passed} of ${latest.summary.total} · ${latest.label}`
              : 'No completed runs'}
          </p>
        </div>
        <div className="card accent-card">
          <p className="metric-label">Holdout</p>
          <HoldoutMetric change={change.data} loading={change.loading} />
        </div>
        <div className="card accent-card">
          <p className="metric-label">Regressions</p>
          {change.loading ? (
            <Spinner label="Loading regressions…" />
          ) : change.data ? (
            <>
              <p className={`metric-value ${change.data.verification.regressions_count ? '' : 'good'}`}>
                {change.data.verification.regressions_count}
              </p>
              <p className="muted">Baseline → candidate</p>
            </>
          ) : (
            <>
              <p className="metric-value">unavailable</p>
              <p className="muted">No verifications yet</p>
            </>
          )}
        </div>
      </div>
      <div className="card accent-card chart-card">
        <div className="section-head">
          <h3>Pass rate by version</h3>
          <span className="muted">Latest run per version</span>
        </div>
        <div className="scroll-x">
          <TrendChart versions={versions} />
        </div>
      </div>
      <div className="overview-bottom">
        <div className="card accent-card">
          <h3>Latest verified change</h3>
          {change.loading ? (
            <Spinner />
          ) : change.error ? (
            <ErrorBox error={change.error} onRetry={change.reload} />
          ) : !change.data ? (
            <p className="muted">No verified changes yet.</p>
          ) : (
            <>
              <h3>
                {change.data.repair.patch_type} patch · {change.data.diagnosis.rule}
              </h3>
              <p className={change.data.verification.accepted ? 'good-text' : 'bad-text'}>
                {change.data.repair.patch_type} patch · target{' '}
                {change.data.verification.target_passed ? 'fixed' : 'failed'} · holdout{' '}
                {change.data.verification.holdout_passed ? 'passed' : 'failed'} ·{' '}
                {change.data.verification.accepted ? 'accepted' : 'rejected'}
              </p>
              <button
                type="button"
                className="link"
                onClick={() =>
                  onReviewChange({
                    diagnosisId: change.data.diagnosis.id,
                    repairId: change.data.repair.id,
                    verificationId: change.data.verification.id,
                  })
                }
              >
                Review change →
              </button>
            </>
          )}
        </div>
        <div className="card accent-card">
          <h3>Diagnoses by rule</h3>
          {bars.length === 0 ? (
            <p className="muted">No diagnoses recorded.</p>
          ) : (
            <ul className="bars">
              {bars.map(([rule, n]) => (
                <li key={rule}>
                  <span className="muted">
                    {rule} · {n}
                  </span>
                  <span className="bar">
                    <span style={{ width: `${(n / barMax) * 100}%` }} />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function HoldoutMetric({ change, loading }) {
  const summary = useFetch(
    change ? `holdout-${change.verification.candidate_run_id}` : null,
    () => api(`/v1/runs/${change.verification.candidate_run_id}/summary`),
  );
  if (loading || summary.loading) return <Spinner label="Loading holdout…" />;
  if (!change) {
    return (
      <>
        <p className="metric-value">unavailable</p>
        <p className="muted">Candidate verification</p>
      </>
    );
  }
  if (summary.error || !summary.data)
    return (
      <>
        <p className="metric-value">unavailable</p>
        <p className="muted">Candidate verification</p>
      </>
    );
  const holdout = summary.data.holdout;
  return (
    <>
      <p className="metric-value good">
        {holdout.passed} / {holdout.total}
      </p>
      <p className="muted">Candidate verification</p>
    </>
  );
}
