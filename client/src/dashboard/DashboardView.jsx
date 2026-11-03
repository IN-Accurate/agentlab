import { useEffect, useMemo, useState } from 'react';
import { api, pollJob } from '../api.js';
import { useFetch } from '../hooks.js';
import { Empty, ErrorBox, Field, Modal, Spinner } from '../ui.jsx';
import HuggingFaceDialog from './HuggingFaceDialog.jsx';
import OverviewTab from './OverviewTab.jsx';
import RunsTab from './RunsTab.jsx';
import TracesTab from './TracesTab.jsx';
import ImprovementsTab from './ImprovementsTab.jsx';

const TABS = [
  ['overview', 'Overview'],
  ['runs', 'Runs'],
  ['traces', 'Traces'],
  ['improvements', 'Improvements'],
];

const RUN_PRESETS = [
  {
    label: 'Quick check',
    hint: 'Dev tests only, repeatable',
    values: { split: 'dev', temperature: '0', maxTokens: '1024', seed: '42' },
  },
  {
    label: 'Full check',
    hint: 'All tests, repeatable',
    values: { split: '', temperature: '0', maxTokens: '2048', seed: '42' },
  },
  {
    label: 'Final check',
    hint: 'Holdout tests, before release',
    values: { split: 'holdout', temperature: '0', maxTokens: '2048', seed: '42' },
  },
  {
    label: 'Real-world check',
    hint: 'All tests, natural variation',
    values: { split: '', temperature: '0.7', maxTokens: '2048', seed: '' },
  },
];

function RunEvalDialog({ versions, credentialId, onClose, onDone }) {
  const [versionId, setVersionId] = useState(versions[0]?.version_id ?? '');
  const [credential, setCredential] = useState(credentialId ?? '');
  const [values, setValues] = useState(RUN_PRESETS[1].values);
  const { split, temperature, maxTokens, seed } = values;
  const [pending, setPending] = useState(false);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    if (pending || !versionId || !credential) {
      setError('Select a version and provide a credential.');
      return;
    }
    setPending(true);
    setError('');
    try {
      const body = { version_id: versionId, credential_id: credential };
      if (split) body.split = split;
      const params = {};
      if (temperature !== '') params.temperature = Number(temperature);
      if (maxTokens !== '') params.max_tokens = Number(maxTokens);
      if (seed !== '') params.seed = Number(seed);
      body.params = params;
      const projectId = versions.find((v) => v.version_id === versionId)?.project_id;
      const job = await api(`/v1/projects/${projectId}/runs`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      const done = await pollJob(job.job_id, {
        interval: 1000,
        timeout: 600000,
      }).catch((e) => {
        throw e;
      });
      if (done.status === 'failed')
        throw new Error(done.error?.error?.message ?? 'Evaluation failed.');
      setProgress(1);
      onDone(done.result?.run_id ?? done.result?.runId ?? null);
    } catch (e) {
      setError(e?.message ?? 'Could not run the evaluation.');
    } finally {
      setPending(false);
    }
  }

  const setValue = (key) => (e) => setValues((v) => ({ ...v, [key]: e.target.value }));

  return (
    <Modal title="Run evaluation" onClose={onClose}>
      <form onSubmit={submit}>
        <div className="eval-presets" role="group" aria-label="Suggested configurations">
          {RUN_PRESETS.map((preset) => {
            const selected = Object.entries(preset.values).every(([k, v]) => values[k] === v);
            return (
              <button
                key={preset.label}
                type="button"
                className={`eval-preset${selected ? ' selected' : ''}`}
                aria-pressed={selected}
                onClick={() => setValues(preset.values)}
                disabled={pending}
              >
                <strong>{preset.label}</strong>
                <span className="muted">{preset.hint}</span>
              </button>
            );
          })}
        </div>
        <Field label="Version">
          {(id) => (
            <select id={id} value={versionId} onChange={(e) => setVersionId(e.target.value)} disabled={pending}>
              {versions.map((v) => (
                <option key={v.version_id} value={v.version_id}>
                  {v.label}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Credential ID">
          {(id) => (
            <input
              id={id}
              value={credential}
              placeholder="Credential from your connected chat"
              onChange={(e) => setCredential(e.target.value)}
              disabled={pending}
            />
          )}
        </Field>
        <Field label="Split">
          {(id) => (
            <select id={id} value={split} onChange={setValue('split')} disabled={pending}>
              <option value="">All tests</option>
              <option value="dev">Dev</option>
              <option value="holdout">Holdout</option>
            </select>
          )}
        </Field>
        <Field label="Temperature">
          {(id) => (
            <input
              id={id}
              type="number"
              min="0"
              max="2"
              step="0.1"
              value={temperature}
              onChange={setValue('temperature')}
              disabled={pending}
            />
          )}
        </Field>
        <Field label="Max tokens">
          {(id) => (
            <input
              id={id}
              type="number"
              min="1"
              max="1000000"
              step="1"
              value={maxTokens}
              onChange={setValue('maxTokens')}
              disabled={pending}
            />
          )}
        </Field>
        <Field label="Seed">
          {(id) => (
            <input
              id={id}
              type="number"
              step="1"
              value={seed}
              placeholder="Random"
              onChange={setValue('seed')}
              disabled={pending}
            />
          )}
        </Field>
        {error ? (
          <p className="field-error" role="alert">
            {error}
          </p>
        ) : null}
        {pending ? (
          <p className="muted" role="status">
            {progress === 1 ? 'Finishing…' : 'Evaluation running… this can take a while.'}
          </p>
        ) : null}
        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={pending}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={pending}>
            {pending ? 'Running…' : 'Start run'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export default function DashboardView({ dash, patchDash, chatCredentialId, onContinueInChat }) {
  const [dialog, setDialog] = useState(false);
  const [importing, setImporting] = useState(false);
  const projects = useFetch('projects', () => api('/v1/projects?limit=100'));

  useEffect(() => {
    if (!dash.projectId && projects.data?.items?.length)
      patchDash({ projectId: projects.data.items[0].id });
  }, [projects.data, dash.projectId, patchDash]);

  const board = useFetch(
    dash.projectId ? `dashboard-${dash.projectId}` : null,
    () => api(`/v1/projects/${dash.projectId}/dashboard`),
  );

  const versions = useMemo(() => {
    const items = board.data?.versions ?? [];
    return items.map((v) => ({ ...v, project_id: dash.projectId }));
  }, [board.data, dash.projectId]);
  const labels = useMemo(
    () => Object.fromEntries(versions.map((v) => [v.version_id, v.label])),
    [versions],
  );

  if (projects.loading) return <Spinner />;
  if (projects.error) return <ErrorBox error={projects.error} onRetry={projects.reload} />;
  if (!projects.data?.items?.length)
    return (
      <Empty
        title="No projects yet"
        hint="Connect an agent in a chat to create your first project."
      />
    );
  if (!dash.projectId) return <Spinner />;

  const project = projects.data.items.find((p) => p.id === dash.projectId) ?? projects.data.items[0];

  return (
    <div className="dashboard">
      <header className="page-head">
        <select
          className="project-select"
          aria-label="Project"
          value={project.id}
          onChange={(e) =>
            patchDash({
              projectId: e.target.value,
              runId: null,
              resultId: null,
              traceId: null,
              review: null,
              spanId: null,
              runVersion: 'all',
              traceVersion: 'all',
              compareA: null,
              compareB: null,
            })
          }
        >
          {projects.data.items.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {(dash.tab === 'overview' || dash.tab === 'runs') && (
          <div className="connect-actions">
            <button type="button" className="btn-secondary" onClick={() => setImporting(true)}>
              Import from Hugging Face
            </button>
            <button type="button" className="btn-primary btn-accent" onClick={() => setDialog(true)}>
              Run evaluation
            </button>
          </div>
        )}
      </header>
      <nav className="tabs" aria-label="Dashboard">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={dash.tab === key ? 'active' : ''}
            aria-current={dash.tab === key ? 'page' : undefined}
            onClick={() => patchDash({ tab: key })}
          >
            {label}
          </button>
        ))}
      </nav>
      {board.loading ? (
        <Spinner />
      ) : board.error ? (
        <ErrorBox error={board.error} onRetry={board.reload} />
      ) : dash.tab === 'overview' ? (
        <OverviewTab
          projectId={dash.projectId}
          data={board.data}
          onReviewChange={(review) => patchDash({ tab: 'improvements', review })}
        />
      ) : dash.tab === 'runs' ? (
        <RunsTab
          projectId={dash.projectId}
          versions={versions}
          labels={labels}
          dash={dash}
          patchDash={patchDash}
          chatCredentialId={chatCredentialId}
          onOpenTrace={(traceId, verdict) =>
            patchDash({ tab: 'traces', traceId, traceVerdict: verdict ?? null })
          }
          onOpenReview={(review) => {
            board.reload();
            patchDash({ tab: 'improvements', review });
          }}
          onContinueInChat={onContinueInChat}
        />
      ) : dash.tab === 'traces' ? (
        <TracesTab
          projectId={dash.projectId}
          versions={versions}
          labels={labels}
          dash={dash}
          patchDash={patchDash}
          chatCredentialId={chatCredentialId}
          onOpenReview={(review) => patchDash({ tab: 'improvements', review })}
        />
      ) : (
        <ImprovementsTab
          projectId={dash.projectId}
          labels={labels}
          dash={dash}
          patchDash={patchDash}
          chatCredentialId={chatCredentialId}
          onVerified={board.reload}
        />
      )}
      {importing ? (
        <HuggingFaceDialog
          projectId={project.id}
          onClose={(imported) => {
            setImporting(false);
            if (imported) board.reload();
          }}
        />
      ) : null}
      {dialog ? (
        <RunEvalDialog
          versions={versions}
          credentialId={chatCredentialId}
          onClose={() => setDialog(false)}
          onDone={(runId) => {
            setDialog(false);
            board.reload();
            patchDash({ tab: 'runs', runId, resultId: null, runsNonce: Date.now() });
          }}
        />
      ) : null}
    </div>
  );
}
