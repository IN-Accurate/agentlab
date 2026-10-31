import { useEffect, useMemo, useState } from 'react';
import { api, pollJob } from '../api.js';
import { useFetch } from '../hooks.js';
import { Empty, ErrorBox, Field, Modal, Spinner } from '../ui.jsx';
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

function RunEvalDialog({ versions, credentialId, onClose, onDone }) {
  const [versionId, setVersionId] = useState(versions[0]?.version_id ?? '');
  const [credential, setCredential] = useState(credentialId ?? '');
  const [split, setSplit] = useState('');
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

  return (
    <Modal title="Run evaluation" onClose={onClose}>
      <form onSubmit={submit}>
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
            <select id={id} value={split} onChange={(e) => setSplit(e.target.value)} disabled={pending}>
              <option value="">All tests</option>
              <option value="dev">Dev</option>
              <option value="holdout">Holdout</option>
            </select>
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
          onChange={(e) => patchDash({ projectId: e.target.value, runId: null, resultId: null, traceId: null, review: null })}
        >
          {projects.data.items.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {(dash.tab === 'overview' || dash.tab === 'runs') && (
          <button type="button" className="btn-primary" onClick={() => setDialog(true)}>
            Run evaluation
          </button>
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
          onOpenReview={(review) => patchDash({ tab: 'improvements', review })}
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
        />
      )}
      {dialog ? (
        <RunEvalDialog
          versions={versions}
          credentialId={chatCredentialId}
          onClose={() => setDialog(false)}
          onDone={(runId) => {
            setDialog(false);
            patchDash({ tab: 'runs', runId, resultId: null, runsNonce: Date.now() });
          }}
        />
      ) : null}
    </div>
  );
}
