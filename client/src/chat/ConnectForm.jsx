import { useRef, useState } from 'react';
import { FIRST_DEFAULT_NOTE, defaultFormState, replaceDefaultNote } from '../agentStore.js';
import { api, ApiError } from '../api.js';
import { useFetch } from '../hooks.js';
import { Field, Spinner } from '../ui.jsx';

export default function ConnectForm({
  initialName = '',
  initialEndpoint = '',
  initialModel = '',
  agents = [],
  onConnect,
  onCancel,
}) {
  const [name, setName] = useState(initialName);
  const [endpoint, setEndpoint] = useState(initialEndpoint);
  const [model, setModel] = useState(initialModel);
  const [apiKey, setApiKey] = useState('');
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState('');
  const [pending, setPending] = useState(false);
  const [ownKey, setOwnKey] = useState(false);
  const [wantDefault, setWantDefault] = useState(false);
  const form = defaultFormState(agents);
  const stash = useRef(null);
  const defaults = useFetch('default-credential', () => api('/v1/credentials/default'));
  const hasDefault = !!defaults.data?.available;
  const useDefault = hasDefault && !ownKey;

  function editOwn(setValue, value) {
    setOwnKey(true);
    setValue(value);
  }

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
    if (pending || (!useDefault && !validate())) return;
    setPending(true);
    setFormError('');
    try {
      const agentEndpoint = useDefault ? defaults.data.base_url : endpoint.trim();
      const agentModel = useDefault ? defaults.data.model : model.trim();
      const agentName = name.trim();
      const signature = JSON.stringify([agentName, useDefault ? 'default' : [endpoint, model, apiKey]]);
      if (!stash.current || stash.current.signature !== signature)
        stash.current = { signature };
      const s = stash.current;
      s.credentialId ??= (
        await api(useDefault ? '/v1/credentials/default' : '/v1/credentials', {
          method: 'POST',
          body: useDefault
            ? '{}'
            : JSON.stringify({
                provider: 'custom',
                base_url: agentEndpoint,
                model: agentModel,
                api_key: apiKey,
              }),
        })
      ).credential_id;
      s.projectId ??= (
        await api('/v1/projects', {
          method: 'POST',
          body: JSON.stringify({ name: agentName || 'New chat' }),
        })
      ).id;
      s.versionId ??= (
        await api(`/v1/projects/${s.projectId}/versions`, { method: 'POST', body: '{}' })
      ).id;
      const result = await api(`/v1/projects/${s.projectId}/agent/test-connection`, {
        method: 'POST',
        body: JSON.stringify({
          credential_id: s.credentialId,
          agent: { type: 'openai_compatible', base_url: agentEndpoint, model: agentModel },
        }),
      });
      if (!result?.ok) {
        setFormError(
          typeof result?.error === 'string'
            ? result.error
            : 'Connection failed. Check the details and retry.',
        );
        setApiKey('');
        return;
      }
      stash.current = null;
      setName(initialName);
      setEndpoint(initialEndpoint);
      setModel(initialModel);
      setApiKey('');
      setErrors({});
      setFormError('');
      setWantDefault(false);
      onConnect(
        {
          name: agentName,
          projectId: s.projectId,
          versionId: s.versionId,
          credentialId: s.credentialId,
          endpoint: agentEndpoint,
          model: agentModel,
          ownProject: true,
        },
        { setAsDefault: !form.isFirst && wantDefault },
      );
    } catch (error) {
      setFormError(
        error instanceof ApiError ? error.message : 'Connection failed. Check the details and retry.',
      );
      setApiKey('');
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="card connect-form" onSubmit={submit} noValidate>
      <h2>Connect agent</h2>
      <Field label="Agent name (optional)">
        {(id) => (
          <input
            id={id}
            value={name}
            maxLength={200}
            placeholder="e.g. Support bot"
            onChange={(e) => setName(e.target.value)}
            disabled={pending}
            autoComplete="off"
          />
        )}
      </Field>
      {hasDefault ? (
        <fieldset className="connect-modes" disabled={pending}>
          <label className={`connect-mode${useDefault ? ' selected' : ''}`}>
            <input
              type="radio"
              name="connect-mode"
              checked={useDefault}
              onChange={() => setOwnKey(false)}
            />
            Default Meta key
          </label>
          <label className={`connect-mode${useDefault ? '' : ' selected'}`}>
            <input
              type="radio"
              name="connect-mode"
              checked={!useDefault}
              onChange={() => setOwnKey(true)}
            />
            My own key
          </label>
        </fieldset>
      ) : null}
      {useDefault ? (
        <dl className="agent-details">
          <dt>Endpoint</dt>
          <dd>{defaults.data.base_url}</dd>
          <dt>Model</dt>
          <dd>{defaults.data.model}</dd>
        </dl>
      ) : (
        <>
          <Field label="Endpoint" error={errors.endpoint}>
            {(id) => (
              <input
                id={id}
                type="url"
                value={endpoint}
                placeholder="https://your-agent.com/v1"
                onChange={(e) => editOwn(setEndpoint, e.target.value)}
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
                onChange={(e) => editOwn(setModel, e.target.value)}
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
                onChange={(e) => editOwn(setApiKey, e.target.value)}
                disabled={pending}
                autoComplete="new-password"
              />
            )}
          </Field>
        </>
      )}
      {!form.isFirst ? (
        <div className="field">
          <label className={`connect-mode${wantDefault ? ' selected' : ''}`}>
            <input
              type="checkbox"
              role="switch"
              checked={wantDefault}
              onChange={(e) => setWantDefault(e.target.checked)}
              disabled={pending}
            />
            Set as default agent
          </label>
          {wantDefault && form.defaultName ? (
            <p className="muted">{replaceDefaultNote(form.defaultName)}</p>
          ) : null}
        </div>
      ) : null}
      {form.isFirst ? <p className="muted">{FIRST_DEFAULT_NOTE}</p> : null}
      <div className="connect-actions">
        <button type="submit" className="btn-primary" disabled={pending}>
          {pending ? <Spinner label="Connecting…" /> : 'Connect agent'}
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
      <p className="muted">
        {useDefault
          ? 'Uses the key stored on the server. It is never sent to this browser.'
          : 'Your key is encrypted and expires after 2 hours.'}
      </p>
    </form>
  );
}
