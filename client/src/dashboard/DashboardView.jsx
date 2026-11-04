import { useEffect, useMemo, useState } from 'react';
import { api, pollJob } from '../api.js';
import { useFetch } from '../hooks.js';
import { PROJECT_RESET, fetchOwnProjects, ownProjectIds, selectedProject } from './projectScope.js';
import { Empty, ErrorBox, Field, Spinner } from '../ui.jsx';
import { Modal } from '../modal.jsx';
import HuggingFaceDialog from './HuggingFaceDialog.jsx';
import OverviewTab from './OverviewTab.jsx';
import RunsTab from './RunsTab.jsx';
import TracesTab from './TracesTab.jsx';
import ImprovementsTab from './ImprovementsTab.jsx';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';

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
            <NativeSelect wrapperClassName="w-full" id={id} value={versionId} onChange={(e) => setVersionId(e.target.value)} disabled={pending}>
              {versions.map((v) => (
                <option key={v.version_id} value={v.version_id}>
                  {v.label}
                </option>
              ))}
            </NativeSelect>
          )}
        </Field>
        <Field label="Credential ID">
          {(id) => (
            <Input
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
            <NativeSelect wrapperClassName="w-full" id={id} value={split} onChange={setValue('split')} disabled={pending}>
              <option value="">All tests</option>
              <option value="dev">Dev</option>
              <option value="holdout">Holdout</option>
            </NativeSelect>
          )}
        </Field>
        <Field label="Temperature">
          {(id) => (
            <Input
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
            <Input
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
            <Input
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
          <Spinner label={progress === 1 ? 'Finishing…' : 'Running evaluation…'} />
        ) : null}
        <div className="modal-actions">
          <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" variant="default" disabled={pending}>
            {pending ? 'Running…' : 'Start run'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export default function DashboardView({
  dash,
  patchDash,
  agents,
  chatCredentialId,
  onContinueInChat,
  onOpenAgentManager,
  showToast,
  onTestInNewChat,
}) {
  const [dialog, setDialog] = useState(false);
  const [importing, setImporting] = useState(false);
  const [pendingDeleteProject, setPendingDeleteProject] = useState(false);
  const [deletingProject, setDeletingProject] = useState(false);
  const ids = useMemo(() => ownProjectIds(agents), [agents]);
  const projects = useFetch(`own-projects-${ids.join(',')}`, () => fetchOwnProjects(api, ids));
  const mine = projects.data ?? [];
  const project = selectedProject(mine, dash.projectId);

  useEffect(() => {
    // Keep the data and the picker on the same project: a remembered project that is
    // no longer one of the user's agents falls back to their first agent.
    if (project && project.id !== dash.projectId) patchDash({ projectId: project.id, ...PROJECT_RESET });
  }, [project, dash.projectId, patchDash]);

  const board = useFetch(
    project && dash.projectId === project.id ? `dashboard-${dash.projectId}` : null,
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
  if (!mine.length)
    return (
      <div className="chat-narrow">
        <Empty
          title="No agents yet"
          hint="The dashboard shows the agents you connect in Agent Manager."
        />
        <Button type="button" variant="brand" onClick={onOpenAgentManager}>
          Open Agent Manager
        </Button>
      </div>
    );
  if (dash.projectId !== project.id) return <Spinner />;

  async function deleteCurrentProject() {
    if (!project?.id || deletingProject) return;
    setDeletingProject(true);
    try {
      await api(`/v1/projects/${project.id}`, { method: 'DELETE' });
      const remaining = mine.filter((p) => p.id !== project.id);
      patchDash({ projectId: remaining[0]?.id ?? null, ...PROJECT_RESET });
      projects.reload();
      if (showToast) {
        showToast({ message: `Project "${project.name}" deleted` });
      }
    } catch (err) {
      if (showToast) {
        showToast({ message: `Failed to delete project: ${err.message}` });
      }
    } finally {
      setDeletingProject(false);
      setPendingDeleteProject(false);
    }
  }

  return (
    <div className="dashboard">
      <header className="page-head">
        <div className="project-select-group">
          <select
            className="project-select"
            aria-label="Project"
            value={project.id}
            // Switching mid-delete would let the deletion's fallback override the new choice.
            disabled={deletingProject}
            onChange={(e) => {
              setPendingDeleteProject(false);
              patchDash({ projectId: e.target.value, ...PROJECT_RESET });
            }}
          >
            {mine.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
          {pendingDeleteProject ? (
            <span className="project-del-confirm" role="group" aria-label="Confirm project deletion">
              <span className="project-del-text">Delete &ldquo;{project.name}&rdquo;?</span>
              <button
                type="button"
                className="project-del-btn-yes"
                disabled={deletingProject}
                onClick={deleteCurrentProject}
              >
                {deletingProject ? 'Deleting…' : 'Delete'}
              </button>
              <button
                type="button"
                className="project-del-btn-cancel"
                disabled={deletingProject}
                onClick={() => setPendingDeleteProject(false)}
              >
                Cancel
              </button>
            </span>
          ) : (
            <button
              type="button"
              className="project-del-trigger"
              title={`Delete project "${project.name}"`}
              aria-label={`Delete project "${project.name}"`}
              onClick={() => setPendingDeleteProject(true)}
            >
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
            </button>
          )}
        </div>
        {(dash.tab === 'overview' || dash.tab === 'runs') && (
          <div className="connect-actions">
            <Button type="button" variant="brand-outline" onClick={() => setImporting(true)}>
              Import from Hugging Face
            </Button>
            <Button type="button" variant="brand" onClick={() => setDialog(true)}>
              Run evaluation
            </Button>
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
      {board.loading && board.data ? <Spinner label="Refreshing dashboard…" /> : null}
      {board.loading && !board.data ? (
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
          showToast={showToast}
          onTestInNewChat={onTestInNewChat}
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
