import { useRef, useState } from 'react';
import { api, ApiError } from '../api.js';
import { Field } from '../ui.jsx';

export default function ConnectForm({ initialEndpoint = '', initialModel = '', onConnect, onCancel }) {
  const [endpoint, setEndpoint] = useState(initialEndpoint);
  const [model, setModel] = useState(initialModel);
  const [apiKey, setApiKey] = useState('');
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState('');
  const [pending, setPending] = useState(false);
  const stash = useRef(null);

  function validate() {
    const next = {};
    if (!/^https?:\/\/.+/.test(endpoint.trim()))
      next.endpoint = 'Enter a valid http(s) URL.';
    if (!model.trim()) next.model = 'Enter a model name.';
    if (apiKey.length < 8) next.apiKey = 'API key must be at least 8 characters.';
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function submit(event) {
    event.preventDefault();
    if (pending || !validate()) return;
    setPending(true);
    setFormError('');
    try {
      if (!stash.current || stash.current.signature !== `${endpoint}|${model}|${apiKey}`)
        stash.current = { signature: `${endpoint}|${model}|${apiKey}` };
      const s = stash.current;
      s.credentialId ??= (
        await api('/v1/credentials', {
          method: 'POST',
          body: JSON.stringify({
            provider: 'custom',
            base_url: endpoint.trim(),
            model: model.trim(),
            api_key: apiKey,
          }),
        })
      ).credential_id;
      s.projectId ??= (
        await api('/v1/projects', {
          method: 'POST',
          body: JSON.stringify({ name: 'New chat' }),
        })
      ).id;
      s.versionId ??= (
        await api(`/v1/projects/${s.projectId}/versions`, { method: 'POST', body: '{}' })
      ).id;
      const result = await api(`/v1/projects/${s.projectId}/agent/test-connection`, {
        method: 'POST',
        body: JSON.stringify({
          credential_id: s.credentialId,
          agent: { type: 'openai_compatible', base_url: endpoint.trim(), model: model.trim() },
        }),
      });
      if (!result?.ok) {
        setFormError(
          typeof result?.error === 'string'
            ? result.error
            : 'Connection failed. Check the details and retry.',
        );
        return;
      }
      stash.current = null;
      onConnect({
        projectId: s.projectId,
        versionId: s.versionId,
        credentialId: s.credentialId,
        endpoint: endpoint.trim(),
        model: model.trim(),
        ownProject: true,
      });
    } catch (error) {
      setFormError(
        error instanceof ApiError ? error.message : 'Connection failed. Check the details and retry.',
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="card connect-form" onSubmit={submit} noValidate>
      <h2>Connect agent</h2>
      <Field label="Endpoint" error={errors.endpoint}>
        {(id) => (
          <input
            id={id}
            type="url"
            value={endpoint}
            placeholder="https://your-agent.com/v1"
            onChange={(e) => setEndpoint(e.target.value)}
            disabled={pending}
            autoComplete="url"
          />
        )}
      </Field>
      <Field label="Model" error={errors.model}>
        {(id) => (
          <input
            id={id}
            value={model}
            placeholder="e.g. llama-3.3-70b"
            onChange={(e) => setModel(e.target.value)}
            disabled={pending}
            autoComplete="off"
          />
        )}
      </Field>
      <Field label="API key" error={errors.apiKey}>
        {(id) => (
          <input
            id={id}
            type="password"
            value={apiKey}
            placeholder="Enter API key"
            onChange={(e) => setApiKey(e.target.value)}
            disabled={pending}
            autoComplete="new-password"
          />
        )}
      </Field>
      <div className="connect-actions">
        <button type="submit" className="btn-primary" disabled={pending}>
          {pending ? 'Connecting…' : 'Connect agent'}
        </button>
        {onCancel ? (
          <button type="button" className="btn-secondary" onClick={onCancel} disabled={pending}>
            Cancel
          </button>
        ) : null}
      </div>
      {formError ? (
        <p className="field-error" role="alert">
          {formError}
        </p>
      ) : null}
      <p className="muted">Your key is encrypted and expires after 2 hours.</p>
    </form>
  );
}
