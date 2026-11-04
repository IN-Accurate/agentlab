import { useState } from 'react';
import { api, pollJob } from '../api.js';
import { useFetch } from '../hooks.js';
import { Empty, ErrorBox, Spinner, Verdict, shortId } from '../ui.jsx';
import { Button } from '@/components/ui/button';
import { NativeSelect } from '@/components/ui/native-select';

function flatten(config, prefix = '') {
  const out = {};
  for (const [key, value] of Object.entries(config ?? {})) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === 'object' && !Array.isArray(value)) Object.assign(out, flatten(value, path));
    else out[path] = value;
  }
  return out;
}

function diffConfigs(before, after) {
  const a = flatten(before);
  const b = flatten(after);
  const removed = [];
  const added = [];
  const changed = [];
  for (const [path, value] of Object.entries(a)) {
    if (!(path in b)) removed.push([path, value]);
    else if (JSON.stringify(value) !== JSON.stringify(b[path])) changed.push([path, value, b[path]]);
  }
  for (const [path, value] of Object.entries(b)) {
    if (!(path in a)) added.push([path, value]);
  }
  return { removed, added, changed };
}

function fmtLatency(ms) {
  if (ms === null || ms === undefined) return 'unavailable';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

async function projectRepairs(projectId) {
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
  const settled = await Promise.allSettled(
    all.map((repair) =>
      api(`/v1/diagnoses/${repair.diagnosis_id}`).then((diagnosis) => ({ repair, diagnosis })),
    ),
  );
  return settled
    .filter((s) => s.status === 'fulfilled')
    .map((s) => s.value)
    .sort((a, b) => (String(a.repair.created_at) < String(b.repair.created_at) ? 1 : -1));
}

async function loadChain(projectId, review) {
  const repairs = await projectRepairs(projectId);
  if (!repairs.length) return { repairs, chain: null };
  let picked = null;
  if (review?.repairId) picked = repairs.find((r) => r.repair.id === review.repairId) ?? null;
  if (!picked && review?.diagnosisId)
    picked = repairs.find((r) => r.diagnosis.id === review.diagnosisId) ?? null;
  if (!picked && review?.verificationId) {
    const verification = await api(`/v1/verifications/${review.verificationId}`);
    picked = repairs.find((r) => r.repair.id === verification.repair_id) ?? null;
    if (picked) {
      return { repairs, chain: await finishChain(picked, verification, verification) };
    }
  }
  // A requested diagnosis with no repair must not fall back to an unrelated one.
  if (!picked && review?.diagnosisId) return { repairs, chain: null };
  picked ??= repairs[0];
  const scored = await api(`/v1/verifications?repair_id=${picked.repair.id}&limit=50`);
  const verification = [...(scored.items ?? [])].sort((a, b) =>
    String(a.created_at) < String(b.created_at) ? 1 : -1,
  )[0];
  if (!verification) return { repairs, chain: await finishChain(picked, null, null) };
  const scorecard = review?.verificationId
    ? await api(`/v1/verifications/${review.verificationId}`)
    : await api(`/v1/verifications/${verification.id}`);
  return { repairs, chain: await finishChain(picked, scorecard, scorecard) };
}

async function finishChain(picked, verification, scorecard) {
  const candidate = await api(`/v1/versions/${picked.repair.new_version_id}`);
  const baselineId = candidate.parent_version_id;
  const [diff, lineage] = await Promise.all([
    baselineId
      ? api(`/v1/versions/${baselineId}/diff/${candidate.id}`)
      : Promise.resolve({ before: {}, after: candidate.config ?? {} }),
    api(`/v1/versions/${candidate.id}/lineage`).catch(() => ({ items: [] })),
  ]);
  return { ...picked, verification, scorecard, candidate, baselineId, diff, lineage };
}

export default function ImprovementsTab({
  projectId,
  labels,
  dash,
  patchDash,
  chatCredentialId,
  onVerified,
}) {
  const [verifying, setVerifying] = useState(false);
  const [actionError, setActionError] = useState('');
  const loaded = useFetch(`review-${projectId}-${JSON.stringify(dash.review ?? null)}`, () =>
    loadChain(projectId, dash.review),
  );

  if (loaded.loading) return <Spinner />;
  if (loaded.error) return <ErrorBox error={loaded.error} onRetry={loaded.reload} />;
  const { repairs, chain } = loaded.data;
  const repairError = dash.review?.repairError ? (
    <p className="field-error" role="alert">
      Diagnosis saved, but no repair was created: {dash.review.repairError}
    </p>
  ) : null;
  if (!chain)
    return (
      <>
        {repairError}
        {repairs.length ? (
          <Empty
            title="No repair for this diagnosis"
            hint="This project has earlier repairs you can review."
            action={
              <Button
                type="button"
                variant="outline"
                onClick={() => patchDash({ review: { repairId: repairs[0].repair.id } })}
              >
                Review latest repair
              </Button>
            }
          />
        ) : (
          <Empty
            title="No improvement reviews yet"
            hint="Diagnose a failed result or trace to propose your first change."
          />
        )}
      </>
    );

  const { diagnosis, repair, verification, scorecard, candidate, baselineId, diff, lineage } = chain;
  const candLabel = labels[candidate.id] ?? shortId(candidate.id);
  const baseLabel = (baselineId && labels[baselineId]) || 'v?';
  const replay = diagnosis.evidence?.replay ?? null;
  const changes = diff ? diffConfigs(diff.before, diff.after) : { removed: [], added: [], changed: [] };

  async function verify() {
    if (verifying) return;
    setVerifying(true);
    setActionError('');
    try {
      const body = chatCredentialId ? { credential_id: chatCredentialId } : {};
      const job = await api(`/v1/repairs/${repair.id}/verify`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      const done = await pollJob(job.job_id, { interval: 1500, timeout: 600000 });
      if (done.status === 'failed') throw new Error(done.error?.error?.message ?? 'Verification failed.');
      onVerified();
      const next = { ...dash.review, repairId: repair.id, verificationId: null };
      patchDash({ review: next });
      if (JSON.stringify(next) === JSON.stringify(dash.review ?? null)) loaded.reload();
    } catch (e) {
      setActionError(e?.message ?? 'Could not verify this repair.');
    } finally {
      setVerifying(false);
    }
  }

  return (
    <div className="improvements">
      {repairError}
      <h2>Did the change actually fix it?</h2>
      <p className="muted">Review the controlled replay, candidate patch, and verification verdict.</p>
      <div className="section-head">
        <h3>
          {repair.patch_type} repair · {baseLabel} → {candLabel}
        </h3>
        <span>
          {verification ? (
            <Verdict value={verification.accepted ? 'Accepted' : 'Rejected'} />
          ) : (
            <span className="muted">Not verified</span>
          )}
        </span>
      </div>
      <div className="card accent-card">
        <label className="muted">
          Review{' '}
          <NativeSelect wrapperClassName="w-full"
            value={repair.id}
            onChange={(e) => patchDash({ review: { repairId: e.target.value } })}
          >
            {repairs.map(({ repair: r, diagnosis: d }) => (
              <option key={r.id} value={r.id}>
                {r.patch_type} · {d.rule} · {shortId(r.id)}
              </option>
            ))}
          </NativeSelect>
        </label>
      </div>
      <ol className="progression">
        <li>
          <strong>1 Diagnose</strong>
          <span className="muted">
            {diagnosis.rule}
            {diagnosis.confidence !== null && diagnosis.confidence !== undefined
              ? ` · ${Number(diagnosis.confidence).toFixed(2)} confidence`
              : ''}
          </span>
        </li>
        <li>
          <strong>2 Replay</strong>
          <span className="muted">
            {replay
              ? `${replay.changed_variable ?? 'variable'} · outcome ${replay.outcome_flipped ? 'flipped' : 'held'}`
              : 'Replay not recorded'}
          </span>
        </li>
        <li>
          <strong>3 Repair</strong>
          <span className="muted">
            {repair.patch_type} patch · candidate {candLabel}
          </span>
        </li>
        <li>
          <strong>4 Verify</strong>
          <span className="muted">
            {verification
              ? `Target ${verification.target_passed ? 'passed' : 'failed'} · holdout ${
                  verification.holdout_passed ? 'passed' : 'failed'
                }`
              : 'Not run yet'}
          </span>
        </li>
      </ol>
      <div className="section-head">
        <h3>Configuration diff</h3>
        <details className="lineage">
          <summary className="link">View lineage →</summary>
          <ul>
            {(lineage.items ?? []).map((v) => (
              <li key={v.id} className="muted">
                {labels[v.id] ?? shortId(v.id)} · {v.created_at ? new Date(v.created_at).toLocaleString() : ''}
              </li>
            ))}
          </ul>
        </details>
      </div>
      <div className="diff-grid">
        <div className="diff-before">
          <p className="metric-label bad-text">Baseline {baseLabel}</p>
          <DiffLines lines={changes.removed} sign="−" changed={changes.changed} side={0} />
        </div>
        <div className="diff-after">
          <p className="metric-label good-text">Candidate {candLabel}</p>
          <DiffLines lines={changes.added} sign="+" changed={changes.changed} side={1} />
        </div>
      </div>
      {verification && scorecard ? (
        <>
          <div className="section-head">
            <h3>Verification scorecard</h3>
            <span className="muted">
              {shortId(scorecard.baseline_run_id ?? verification.baseline_run_id)} →{' '}
              {shortId(scorecard.candidate_run_id ?? verification.candidate_run_id)}
            </span>
          </div>
          <div className="scroll-x card-panel">
            <table className="scorecard">
              <tbody>
                <tr>
                  <td>Target test</td>
                  <td>
                    <Verdict value={verification.target_passed ? 'Passed' : 'Failed'} />
                  </td>
                </tr>
                <ScoreRow
                  label="Overall pass rate"
                  a={scorecard.baseline}
                  b={scorecard.candidate}
                  render={(s) => (s ? `${s.passed} / ${s.total}` : 'unavailable')}
                />
                <ScoreRow
                  label="Holdout"
                  a={scorecard.baseline?.holdout}
                  b={scorecard.candidate?.holdout}
                  render={(s) => (s ? `${s.passed} / ${s.total}` : 'unavailable')}
                />
                <ScoreRow
                  label="LLM latency p50"
                  a={scorecard.baseline}
                  b={scorecard.candidate}
                  render={(s) => (s ? fmtLatency(s.latency_p50) : 'unavailable')}
                />
              </tbody>
            </table>
          </div>
          <p className={`verdict-banner ${verification.accepted ? 'good' : 'bad'}`}>
            {verification.accepted ? 'Accepted' : 'Rejected'} · Target{' '}
            {verification.target_passed ? 'passed' : 'failed'} · {verification.regressions_count}{' '}
            regressions · Holdout {verification.holdout_passed ? 'passed' : 'failed'}
          </p>
        </>
      ) : (
        <div className="card">
          <p>This repair has not been verified yet.</p>
          <Button type="button" variant="default" disabled={verifying} onClick={verify}>
            {verifying ? 'Verifying…' : 'Verify repair'}
          </Button>
          {actionError ? (
            <p className="field-error" role="alert">
              {actionError}
            </p>
          ) : null}
        </div>
      )}
    </div>
  );
}

function DiffLines({ lines, sign, changed, side }) {
  const items = [
    ...lines.map(([path, value]) => ({ path, value })),
    ...changed.map(([path, before, after]) => ({ path, value: side === 0 ? before : after })),
  ];
  if (!items.length) return <p className="muted">No changes.</p>;
  return (
    <ul className="diff-lines">
      {items.map(({ path, value }) => (
        <li key={path}>
          <span aria-hidden="true">{sign} </span>
          {path}: {JSON.stringify(value)}
        </li>
      ))}
    </ul>
  );
}

function ScoreRow({ label, a, b, render }) {
  return (
    <tr>
      <td>{label}</td>
      <td className="muted">{render(a)}</td>
      <td className="good-text">{render(b)}</td>
    </tr>
  );
}
