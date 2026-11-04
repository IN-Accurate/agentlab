import { useEffect, useRef, useState } from 'react';
import { FIRST_DEFAULT_NOTE, defaultFormState, replaceDefaultNote } from '../agentStore.js';
import { api, ApiError } from '../api.js';
import { useFetch } from '../hooks.js';
import { Field, Spinner } from '../ui.jsx';
import { isCredentialExpired } from '../credentialErrors.js';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

export default function ConnectForm({
  initialName = '',
  initialEndpoint = '',
  initialModel = '',
  agents = [],
  reconnectAgent = null,
  onConnect,
  onCancel,
}) {
  const reconnecting = Boolean(reconnectAgent);
  const [name, setName] = useState(reconnectAgent?.name ?? initialName);
  const [endpoint, setEndpoint] = useState(reconnectAgent?.endpoint ?? initialEndpoint);
  const [model, setModel] = useState(reconnectAgent?.model ?? initialModel);
  const [apiKey, setApiKey] = useState('');
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState('');
  const [pending, setPending] = useState(false);
  const [ownKey, setOwnKey] = useState(reconnecting && reconnectAgent.credentialSource !== 'default');
  const [wantDefault, setWantDefault] = useState(false);
  const form = defaultFormState(agents);
  const stash = useRef(null);
  const aborter = useRef(null);
  const defaults = useFetch('default-credential', () => api('/v1/credentials/default'));
  const hasDefault = !!defaults.data?.available && (!reconnecting || (
    defaults.data.base_url?.replace(/\/+$/, '') === endpoint.replace(/\/+$/, '') && defaults.data.model === model
  ));
  const useDefault = hasDefault && !ownKey;

  useEffect(() => () => aborter.current?.abort(), []);

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
    if (pending || aborter.current || (!useDefault && !validate())) return;
    setPending(true);
    setFormError('');
    const controller = new AbortController();
    aborter.current = controller;
    const request = async (path, options) => {
      const result = await api(path, { ...options, signal: controller.signal });
      if (controller.signal.aborted) throw new DOMException('Connection cancelled', 'AbortError');
      return result;
    };
    try {
      const agentEndpoint = useDefault ? defaults.data.base_url : endpoint.trim();
      const agentModel = useDefault ? defaults.data.model : model.trim();
      const agentName = name.trim();
      const signature = JSON.stringify([agentName, useDefault ? 'default' : [endpoint, model, apiKey]]);
      if (!stash.current || stash.current.signature !== signature)
        stash.current = { signature, projectId: reconnectAgent?.projectId, versionId: reconnectAgent?.versionId };
      const s = stash.current;
      if (!s.credentialId) {
        const credential = await request(useDefault ? '/v1/credentials/default' : '/v1/credentials', {
          method: 'POST',
          body: useDefault
            ? '{}'
            : JSON.stringify({
                provider: 'custom',
                base_url: agentEndpoint,
                model: agentModel,
                api_key: apiKey,
              }),
        });
        s.credentialId = credential.credential_id;
        s.expiresAt = credential.expires_at;
      }
      s.projectId ??= (
        await request('/v1/projects', {
          method: 'POST',
          body: JSON.stringify({ name: agentName || 'New chat' }),
        })
      ).id;
      if (!reconnecting) s.versionId ??= (
        await request(`/v1/projects/${s.projectId}/versions`, { method: 'POST', body: '{}' })
      ).id;
      const result = await request(`/v1/projects/${s.projectId}/agent/test-connection`, {
        method: 'POST',
        body: JSON.stringify({
          credential_id: s.credentialId,
          agent: { type: 'openai_compatible', base_url: agentEndpoint, model: agentModel },
        }),
      });
      if (!result?.ok) {
        s.credentialId = null;
        s.expiresAt = null;
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
          expiresAt: s.expiresAt,
          credentialSource: useDefault ? 'default' : 'custom',
          endpoint: agentEndpoint,
          model: agentModel,
          ownProject: reconnecting ? reconnectAgent.ownProject : true,
        },
        { setAsDefault: !reconnecting && !form.isFirst && wantDefault },
      );
    } catch (error) {
      if (error?.name === 'AbortError') return;
      if (isCredentialExpired(error) && stash.current) {
        stash.current.credentialId = null;
        stash.current.expiresAt = null;
      }
      setFormError(
        error instanceof ApiError ? error.message : 'Connection failed. Check the details and retry.',
      );
      setApiKey('');
    } finally {
      if (!controller.signal.aborted) setPending(false);
      if (aborter.current === controller) aborter.current = null;
    }
  }

  return (
    <form className="card connect-form" onSubmit={submit} noValidate>
      <h2>{reconnecting ? 'Reconnect agent' : 'Connect agent'}</h2>
      {reconnecting ? <p className="muted">Refresh this agent’s connection using the server key or enter your API key again.</p> : null}
      <Field label="Agent name (optional)">
        {(id) => (
          <Input
            id={id}
            value={name}
            maxLength={200}
            placeholder="e.g. Support bot"
            onChange={(e) => setName(e.target.value)}
            disabled={pending}
            readOnly={reconnecting}
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
              <Input
                id={id}
                type="url"
                value={endpoint}
                placeholder="https://your-agent.com/v1"
                onChange={(e) => editOwn(setEndpoint, e.target.value)}
                disabled={pending}
                readOnly={reconnecting}
                autoComplete="url"
              />
            )}
          </Field>
          <Field label="Model" error={errors.model}>
            {(id) => (
              <Input
                id={id}
                value={model}
                placeholder="e.g. llama-3.3-70b"
                onChange={(e) => editOwn(setModel, e.target.value)}
                disabled={pending}
                readOnly={reconnecting}
                autoComplete="off"
              />
            )}
          </Field>
          <Field label="API key" error={errors.apiKey}>
            {(id) => (
              <Input
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
      {!reconnecting && !form.isFirst ? (
        <div className="field">
          <label className={`connect-mode default-agent-toggle${wantDefault ? ' selected' : ''}`}>
            <input
              type="checkbox"
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
      {!reconnecting && form.isFirst ? <p className="muted">{FIRST_DEFAULT_NOTE}</p> : null}
      <div className="connect-actions">
        <Button type="submit" variant="brand" className="flex-1" disabled={pending}>
          {pending ? <Spinner label={reconnecting ? 'Reconnecting…' : 'Connecting…'} /> : reconnecting ? 'Reconnect agent' : 'Connect agent'}
        </Button>
        {onCancel ? (
          <Button type="button" variant="outline" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
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
