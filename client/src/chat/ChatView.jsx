import { useEffect, useRef, useState } from 'react';
import { api, streamChat } from '../api.js';
import ConnectForm from './ConnectForm.jsx';
import Composer from './Composer.jsx';
import TestCard from './TestCard.jsx';

let counter = 0;
const nextId = () => `m${Date.now()}-${counter++}`;

function titleFor(text) {
  const words = text.split(/\s+/).filter(Boolean);
  const head = words.slice(0, 6).join(' ');
  return words.length > 6 ? `${head}…` : head;
}

export default function ChatView({
  chat,
  lastConnection,
  onPatch,
  onConnect,
  onOpenEvidence,
  onOpenReview,
}) {
  const [chatError, setChatError] = useState('');
  const [findingFix, setFindingFix] = useState(null);
  const aborter = useRef(null);
  const scrollRef = useRef(null);
  const conn = chat.connection;

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.messages.length]);

  useEffect(() => () => aborter.current?.abort(), []);

  function patchMessages(map) {
    onPatch(chat.id, (c) => ({ ...c, messages: c.messages.map(map) }));
  }

  async function send(text, context, { kind = 'chat', cardId = null } = {}) {
    if (!conn || chat.streaming) return;
    setChatError('');
    const userMsg = { id: nextId(), role: 'user', text, kind };
    const shell = { id: nextId(), role: 'assistant', text: '', cards: [] };
    const patch = { messages: [...chat.messages, userMsg, shell], streaming: true };
    if (kind === 'chat' && !chat.titleSet) {
      patch.title = titleFor(text);
      patch.titleSet = true;
      if (conn.ownProject) {
        api(`/v1/projects/${conn.projectId}`, {
          method: 'PATCH',
          body: JSON.stringify({ name: patch.title }),
        }).catch(() => {});
      }
    }
    onPatch(chat.id, (c) => ({ ...c, ...patch }));

    const bodyText = context ? `${text}\n\nAdditional context:\n${context}` : text;
    const history = [...chat.messages, userMsg]
      .filter((m) => m.text)
      .slice(-20)
      .map((m) => ({ role: m.role, content: m.text }));
    const acc = { text: '', cards: [] };
    const controller = new AbortController();
    aborter.current = controller;
    const paint = () =>
      patchMessages((m) => (m.id === shell.id ? { ...m, text: acc.text, cards: acc.cards } : m));

    try {
      await streamChat(
        conn.projectId,
        { message: bodyText, history, credential_id: conn.credentialId },
        (event, data) => {
          if (event === 'message.delta') {
            acc.text += data.text ?? '';
            paint();
          } else if (event === 'step.started' || event === 'step.finished') {
            const note = data.step ?? data.label ?? (event === 'step.started' ? 'Working…' : 'Done');
            if (cardId) {
              onPatch(chat.id, (c) => ({
                ...c,
                messages: c.messages.map((m) => ({
                  ...m,
                  cards: m.cards.map((card) =>
                    card.id === cardId ? { ...card, progress: note } : card,
                  ),
                })),
              }));
            }
          } else if (event === 'tool.result') {
            const card = toCard(data, cardId);
            if (card) {
              if (cardId && card.kind === 'test') {
                onPatch(chat.id, (c) => ({
                  ...c,
                  messages: c.messages.map((m) => ({
                    ...m,
                    cards: m.cards.map((old) =>
                      old.id === cardId ? { ...old, ...card } : old,
                    ),
                  })),
                }));
              } else {
                acc.cards.push(card);
                paint();
              }
            }
          } else if (event === 'error') {
            acc.text += `\n\nError: ${data?.error?.message ?? data?.message ?? 'chat failed'}`;
            paint();
          }
        },
        { signal: controller.signal },
      );
    } catch (error) {
      if (error?.name !== 'AbortError') {
        const message =
          error?.code === 'evaluator_not_configured' || error?.status === 503
            ? 'No evaluator is configured on the backend, so this chat cannot run. Connect the evaluator service and retry.'
            : (error?.message ?? 'Chat failed.');
        onPatch(chat.id, (c) => ({
          ...c,
          messages: c.messages.map((m) =>
            m.id === shell.id ? { ...m, text: `${acc.text}\n\nError: ${message}`.trim() } : m,
          ),
        }));
      }
    } finally {
      aborter.current = null;
      onPatch(chat.id, (c) => {
        const out = { ...c, streaming: false };
        if (kind === 'chat' && !c.messages.some((m) => m.cards.some((card) => card.kind === 'test'))) {
          out.messages = [
            ...c.messages,
            {
              id: nextId(),
              role: 'assistant',
              text: '',
              cards: [
                { id: nextId(), kind: 'test', status: 'ready', title: 'New test', expected: text },
              ],
            },
          ];
        }
        if (cardId) {
          out.messages = out.messages.map((m) => ({
            ...m,
            cards: m.cards.map((card) =>
              card.id === cardId && card.status === 'running' ? { ...card, status: 'ready' } : card,
            ),
          }));
        }
        return out;
      });
    }
  }

  function toCard(tool, targetId) {
    const data = tool?.data ?? {};
    if (tool?.type === 'run_summary') {
      const payload = data.data ?? data;
      return {
        id: targetId ?? nextId(),
        kind: 'test',
        status: 'done',
        result: {
          verdict: payload.verdict ?? (payload.passed ? 'pass' : 'fail'),
          rule: payload.rule ?? payload.failure_type ?? '',
          expected: payload.expected ?? '',
          actual: payload.actual ?? payload.answer ?? '—',
          explanation: payload.explanation ?? payload.diagnosis ?? '',
          runId: payload.run_id ?? payload.runId ?? null,
          resultId: payload.run_result_id ?? payload.result_id ?? payload.resultId ?? null,
          traceId: payload.trace_id ?? payload.traceId ?? null,
        },
      };
    }
    if (['diagnosis', 'replay', 'repair', 'scorecard'].includes(tool?.type)) {
      const payload = data.data ?? {};
      return {
        id: nextId(),
        kind: 'info',
        title: tool.type,
        text: payload.summary ?? payload.message ?? '',
        ref: payload.id ?? payload[`${tool.type}_id`] ?? null,
        refType: tool.type,
      };
    }
    return null;
  }

  async function runTest(cardId, context) {
    const card = chat.messages.flatMap((m) => m.cards).find((c) => c.id === cardId);
    if (!card || card.status === 'running') return;
    onPatch(chat.id, (c) => ({
      ...c,
      messages: c.messages.map((m) => ({
        ...m,
        cards: m.cards.map((old) =>
          old.id === cardId ? { ...old, status: 'running', progress: 'Running test…' } : old,
        ),
      })),
    }));
    await send(`Run test: ${card.expected}`, context || card.context || '', {
      kind: 'run',
      cardId,
    });
  }

  async function findFix(card) {
    const resultId = card.result?.resultId;
    if (!resultId || !conn || findingFix) return;
    setFindingFix(card.id);
    try {
      const diagnosis = await api(`/v1/run-results/${resultId}/diagnose`, {
        method: 'POST',
        body: JSON.stringify({ credential_id: conn.credentialId }),
      });
      onOpenReview({ diagnosisId: diagnosis.id });
    } catch (error) {
      setChatError(error?.message ?? 'Could not start the fix flow.');
    } finally {
      setFindingFix(null);
    }
  }

  return (
    <div className="chat">
      <header className="page-head">
        <h1 className="truncate">{chat.title}</h1>
        <span className="muted">{conn ? 'Agent connected' : 'Not connected'}</span>
      </header>
      <div className="chat-body" ref={scrollRef}>
        {!conn ? (
          <div className="chat-narrow">
            <h2 className="hero">Let&rsquo;s connect your agent</h2>
            <p className="muted">Add your connection details to start testing.</p>
            <ConnectForm
              initialEndpoint={lastConnection?.endpoint ?? ''}
              initialModel={lastConnection?.model ?? ''}
              onConnect={(connection) => onConnect(chat.id, connection)}
            />
          </div>
        ) : chat.messages.length === 0 ? (
          <div className="chat-narrow">
            <p className="connected-note">Agent connected</p>
            <h2 className="hero">What should we test?</h2>
            <p className="muted">Describe expected behavior or paste a response that went wrong.</p>
          </div>
        ) : (
          <div className="chat-narrow messages">
            {chat.messages.map((m) =>
              m.role === 'user' ? (
                <div key={m.id} className="bubble-user">
                  {m.text}
                </div>
              ) : (
                <div key={m.id} className="assistant-block">
                  <p className="assistant-label">TestLab</p>
                  {m.text ? <p className="assistant-text">{m.text}</p> : null}
                  {m.cards.map((card) =>
                    card.kind === 'test' ? (
                      <TestCard
                        key={card.id}
                        card={card}
                        findingFix={findingFix === card.id}
                        onRun={runTest}
                        onViewEvidence={() =>
                          onOpenEvidence({ runId: card.result?.runId ?? null, resultId: card.result?.resultId ?? null })
                        }
                        onFindFix={() => findFix(card)}
                      />
                    ) : (
                      <div key={card.id} className="card info-card">
                        <p className="info-title">{card.title}</p>
                        {card.text ? <p className="muted">{card.text}</p> : null}
                      </div>
                    ),
                  )}
                </div>
              ),
            )}
            {chat.streaming ? (
              <p className="muted" role="status">
                Thinking…
              </p>
            ) : null}
          </div>
        )}
        {chatError ? (
          <div className="chat-narrow">
            <div className="error-box" role="alert">
              <p>{chatError}</p>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => onPatch(chat.id, (c) => ({ ...c, connection: null })) || setChatError('')}
              >
                Reconnect
              </button>
            </div>
          </div>
        ) : null}
      </div>
      <div className="chat-narrow composer-wrap">
        <Composer
          disabled={!conn || chat.streaming}
          placeholder={conn ? 'Ask a follow-up…' : 'Connect your agent to begin'}
          onSend={(text, context) => send(text, context)}
        />
      </div>
    </div>
  );
}
