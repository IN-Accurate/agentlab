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
  for (; ;) {
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
  // When review specifies a diagnosisId that doesn't have a repair yet:
  if (!picked && review?.diagnosisId) {
    try {
      const diagnosis = await api(`/v1/diagnoses/${review.diagnosisId}`);
      let trace = null;
      if (diagnosis?.trace_id) {
        trace = await api(`/v1/traces/${diagnosis.trace_id}`).catch(() => null);
      }
      return {
        repairs,
        chain: {
          diagnosis,
          trace,
          repair: null,
          verification: null,
          scorecard: null,
          candidate: null,
          baselineId: null,
          diff: null,
          lineage: { items: [] },
        },
      };
    } catch {
      return { repairs, chain: null };
    }
  }
  picked ??= repairs[0];
  if (!picked) return { repairs, chain: null };
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

  const { diagnosis, trace, repair, verification, scorecard, candidate, baselineId, diff, lineage } = chain;
  const candLabel = candidate ? (labels[candidate.id] ?? shortId(candidate.id)) : 'Pending';
  const baseLabel = (baselineId && labels[baselineId]) || 'v?';
  const replay = diagnosis.evidence?.replay ?? null;
  const changes = diff ? diffConfigs(diff.before, diff.after) : { removed: [], added: [], changed: [] };

  const [proposingRepair, setProposingRepair] = useState(false);
  const [repairActionError, setRepairActionError] = useState('');

  async function proposeRepairFromDiagnosis() {
    if (proposingRepair || !diagnosis?.id) return;
    setProposingRepair(true);
    setRepairActionError('');
    try {
      const body = chatCredentialId ? { credential_id: chatCredentialId } : {};
      const rep = await api(`/v1/diagnoses/${diagnosis.id}/repairs`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      patchDash({ review: { repairId: rep.id } });
      loaded.reload();
    } catch (e) {
      setRepairActionError(e?.message ?? 'Could not propose repair.');
    } finally {
      setProposingRepair(false);
    }
  }

  async function verify() {
    if (verifying || !repair?.id) return;
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

  if (!repair) {
    return (
      <div className="improvements">
        {repairError}
        <h2>Diagnosis review</h2>
        <p className="muted">Review the root cause diagnosis and generate a candidate repair patch.</p>
        <div className="section-head">
          <h3>
            Diagnosis · {diagnosis.rule}
            {diagnosis.trace_id ? (
              <span className="muted"> (trace tr_{shortId(diagnosis.trace_id)})</span>
            ) : null}
          </h3>
          <span>
            <span className="verdict neutral">Pending repair</span>
          </span>
        </div>
        {repairs.length ? (
          <div className="card accent-card">
            <label className="muted">
              Review other repairs{' '}
              <select
                value=""
                onChange={(e) => patchDash({ review: { repairId: e.target.value } })}
              >
                <option value="" disabled>Viewing current diagnosis</option>
                {repairs.map(({ repair: r, diagnosis: d }) => (
                  <option key={r.id} value={r.id}>
                    {r.patch_type} · {d.rule} · {shortId(r.id)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : null}
        <ol className="progression">
          <li>
            <strong>1 Diagnose</strong>
            <span className="good-text">
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
            <span className="muted">Pending repair proposal</span>
          </li>
          <li>
            <strong>4 Verify</strong>
            <span className="muted">Awaiting candidate patch</span>
          </li>
        </ol>

        <div className="card">
          <div className="section-head">
            <h3>Diagnostic findings</h3>
            <span className="muted">
              Confidence {diagnosis.confidence !== null && diagnosis.confidence !== undefined ? `${(Number(diagnosis.confidence) * 100).toFixed(0)}%` : '—'}
            </span>
          </div>
          {diagnosis.evidence?.rationale ? (
            <p>{diagnosis.evidence.rationale}</p>
          ) : (
            <p className="muted">Classification rule: {diagnosis.rule}</p>
          )}
          {diagnosis.evidence?.suggested_rule ? (
            <p className="muted">Suggested rule: {diagnosis.evidence.suggested_rule}</p>
          ) : null}
          {diagnosis.evidence?.errors?.length ? (
            <div>
              <p className="metric-label bad-text">Errors</p>
              <ul className="bad-text">
                {diagnosis.evidence.errors.map((err, i) => (
                  <li key={i}>{String(err)}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {trace ? (
            <div style={{ marginTop: '16px' }}>
              <p className="metric-label">Trace input</p>
              <p>{trace.input ?? '—'}</p>
              <p className="metric-label">Trace output</p>
              <p className={trace.status === 'ok' ? '' : 'bad-text'}>{trace.output ?? '—'}</p>
            </div>
          ) : null}

          <div style={{ marginTop: '20px' }}>
            <Button
              type="button"
              variant="default"
              disabled={proposingRepair}
              onClick={proposeRepairFromDiagnosis}
            >
              {proposingRepair ? 'Proposing repair…' : 'Propose repair →'}
            </Button>
            {repairActionError ? (
              <p className="field-error" style={{ marginTop: '8px' }}>
                {repairActionError}
              </p>
            ) : null}
          </div>
        </div>
      </div>
    );
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
              ? `Target ${verification.target_passed ? 'passed' : 'failed'} · holdout ${verification.holdout_passed ? 'passed' : 'failed'
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
