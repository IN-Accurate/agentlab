import { Suspense, lazy, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DEFAULT_DONE_NOTE,
  addAgent,
  getDefaultAgent,
  getAgentForChat,
  getNewChatAgent,
  isConnected,
  invalidateCredential,
  loadAgentStore,
  removeAgent,
  repointChats,
  reconcileCredentialMetadata,
  refreshAgentChats,
  restoreChatConnection,
  saveAgentStore,
  setDefault,
} from './agentStore.js';
import { trimChatsForPersist } from './chatStorage.js';
import {
  RECENT_LIMIT,
  chatAgentId,
  chatCreatedAt,
  groupChats,
  visibleChats,
} from './chatGroups.js';
import { loadUi, saveUi } from './uiState.js';
import LogoMark from './LogoMark.jsx';
import { Spinner } from './ui.jsx';
import { api } from './api.js';

const loadChatView = () => import('./chat/ChatView.jsx');
const loadConnectForm = () => import('./chat/ConnectForm.jsx');
const loadDashboardView = () => import('./dashboard/DashboardView.jsx');
const ChatView = lazy(loadChatView);
const ConnectForm = lazy(loadConnectForm);
const DashboardView = lazy(loadDashboardView);

function preloadView(loader) {
  // Navigation still owns error handling if the optional warm-up fails.
  void loader().catch(() => {});
}

const STORE_KEY = 'agentlab.chats.v1';
const OLD_STORE_KEY = 'testlab.chats.v1';
const THEME_KEY = 'agentlab.theme';
const GROUP_KEY = 'agentlab.chatGroup';
const THEME_OPTIONS = ['light', 'dark', 'system'];

function ThemeIcon({ name }) {
  const props = {
    viewBox: '0 0 24 24',
    width: 16,
    height: 16,
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round',
    'aria-hidden': true,
  };
  if (name === 'light')
    return (
      <svg {...props}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  if (name === 'dark')
    return (
      <svg {...props}>
        <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
      </svg>
    );
  return (
    <svg {...props}>
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </svg>
  );
}

function loadChats() {
  try {
    const raw = localStorage.getItem(STORE_KEY) ?? localStorage.getItem(OLD_STORE_KEY);
    if (!raw) return [];
    return JSON.parse(raw).map((c) => ({
      ...c,
      createdAt: chatCreatedAt(c),
      agentId: chatAgentId(c),
      streaming: false,
      messages: (c.messages ?? []).map((m) => ({
        ...m,
        cards: (m.cards ?? []).map((card) =>
          card.status === 'running' ? { ...card, status: 'ready' } : card,
        ),
      })),
    }));
  } catch {
    return [];
  }
}

function persistChats(chats) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(trimChatsForPersist(chats)));
  } catch {
    // Private browsing / quota: conversations last for this tab only.
  }
}

// Memoized on primitives + stable callbacks so streaming updates (which only
// change messages) don't re-render sidebar rows.
const ChatRow = memo(function ChatRow({
  id,
  title,
  selected,
  armed,
  deletable,
  onOpen,
  onArm,
  onConfirm,
}) {
  return (
    <li className="chat-row">
      {armed ? (
        <span
          className="chat-del-confirm"
          onKeyDown={(e) => {
            if (e.key === 'Escape') onArm(null);
          }}
        >
          <span className="chat-del-text">Delete this chat?</span>
          <button type="button" className="chat-del-yes" autoFocus onClick={() => onConfirm(id)}>
            Delete
          </button>
          <button type="button" onClick={() => onArm(null)}>
            Cancel
          </button>
        </span>
      ) : (
        <>
          <button
            type="button"
            className={selected ? 'selected' : ''}
            onClick={() => onOpen(id)}
            title={title}
          >
            {title}
          </button>
          {deletable && (
            <button
              type="button"
              className="chat-del"
              aria-label={`Delete ${title}`}
              title="Delete chat"
              onClick={() => onArm(id)}
            >
              <span aria-hidden="true">×</span>
            </button>
          )}
        </>
      )}
    </li>
  );
});

function newChat(connection) {
  return {
    id: `c${Date.now()}`,
    createdAt: Date.now(),
    title: 'New chat',
    titleSet: false,
    connection: connection
      ? {
          name: connection.name,
          projectId: connection.projectId,
          versionId: connection.versionId,
          credentialId: connection.credentialId,
          credentialSource: connection.credentialSource ?? null,
          endpoint: connection.endpoint,
          model: connection.model,
          ...(connection.memberProjectIds ? { memberProjectIds: connection.memberProjectIds } : {}),
          ownProject: false,
        }
      : null,
    agentId: connection?.id ?? connection?.projectId ?? null,
    messages: [],
    streaming: false,
  };
}

export default function App() {
  // Restore agents first so the first rendered draft has the same connection
  // as Agent Manager, including when chat storage is empty or was evicted.
  const [agents, setAgents] = useState(() => loadAgentStore(localStorage));
  const [chats, setChats] = useState(() => {
    const loaded = loadChats();
    return loaded.length
      ? loaded.map((chat) => restoreChatConnection(chat, agents))
      : [newChat(getNewChatAgent(agents))];
  });
  const [savedUi] = useState(loadUi);
  const [connectNotice, setConnectNotice] = useState(null);
  const [activeId, setActiveId] = useState(savedUi.activeId);
  const [view, setView] = useState(savedUi.view);
  const [dash, setDash] = useState(savedUi.dash);
  const [menuOpen, setMenuOpen] = useState(false);
  const [changingAgent, setChangingAgent] = useState(false);
  const [reconnectingAgentId, setReconnectingAgentId] = useState(null);
  const [chatsExpanded, setChatsExpanded] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [groupMode, setGroupMode] = useState(() => {
    try {
      return localStorage.getItem(GROUP_KEY) === 'agent' ? 'agent' : 'date';
    } catch {
      return 'date';
    }
  });
  const [theme, setTheme] = useState(() => {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      return saved === 'light' || saved === 'dark' ? saved : 'system';
    } catch {
      return 'system';
    }
  });

  const chatsRef = useRef(chats);
  useEffect(() => {
    chatsRef.current = chats;
    // Streams update chats per token; trailing debounce avoids
    // re-serializing every conversation on each one.
    const timer = setTimeout(() => persistChats(chatsRef.current), 500);
    return () => clearTimeout(timer);
  }, [chats]);
  useEffect(() => {
    const flush = () => persistChats(chatsRef.current);
    window.addEventListener('pagehide', flush);
    return () => window.removeEventListener('pagehide', flush);
  }, []);

  useEffect(() => {
    saveAgentStore(localStorage, agents);
  }, [agents]);

  useEffect(() => {
    saveUi({ view, activeId, dash });
  }, [view, activeId, dash]);

  useEffect(() => {
    if (view !== 'agents') setConnectNotice(null);
  }, [view]);

  useEffect(() => {
    try {
      localStorage.setItem(GROUP_KEY, groupMode);
    } catch {
      // Private browsing: grouping lasts for this tab only.
    }
  }, [groupMode]);

  useEffect(() => {
    const root = document.documentElement;
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      root.dataset.theme = theme === 'system' ? (media.matches ? 'dark' : 'light') : theme;
    };
    apply();
    media.addEventListener('change', apply);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // Private browsing: theme lasts for this tab only.
    }
    return () => media.removeEventListener('change', apply);
  }, [theme]);

  // Ticks so key expiry flips status labels without a refresh.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);

  const credentialSignature = JSON.stringify([...new Set(agents.map((a) => a.credentialId).filter(Boolean))]);
  useEffect(() => {
    if (view !== 'agents') return;
    let cancelled = false;
    const controller = new AbortController();
    const inFlight = new Set();
    const check = async (credentialId) => {
      if (inFlight.has(credentialId)) return;
      inFlight.add(credentialId);
      try {
        const metadata = await api(`/v1/credentials/${encodeURIComponent(credentialId)}`, { signal: controller.signal });
        if (!cancelled) setAgents((current) => reconcileCredentialMetadata(current, credentialId, metadata));
      } catch (error) {
        if (!cancelled && error?.code === 'credential_expired') {
          setAgents((current) => invalidateCredential(current, credentialId));
        }
      } finally {
        inFlight.delete(credentialId);
      }
    };
    const reconcile = () => {
      setNowMs(Date.now());
      for (const credentialId of JSON.parse(credentialSignature)) void check(credentialId);
    };
    reconcile();
    window.addEventListener('focus', reconcile);
    return () => {
      cancelled = true;
      controller.abort();
      window.removeEventListener('focus', reconcile);
    };
  }, [view, credentialSignature]);

  const activeChat = (activeId && chats.find((c) => c.id === activeId)) || chats[0] || null;
  const reconnectingAgent = agents.find((agent) => agent.id === reconnectingAgentId) ?? null;
  const connectedAgents = agents.filter((a) => isConnected(a, nowMs));
  const agentKeys = useMemo(
    () => Object.fromEntries(agents.map((a) => [a.projectId, a.credentialId])),
    [agents],
  );
  const chatCredentialId =
    agents.find((a) => a.projectId === dash.projectId)?.credentialId ??
    chats.find((c) => c.connection?.projectId === dash.projectId)?.connection
      ?.credentialId ?? null;

  const patchDash = useCallback((patch) => {
    setDash((d) => ({ ...d, ...patch }));
  }, []);

  const openChat = useCallback((id) => {
    setActiveId(id);
    setView('chat');
    setMenuOpen(false);
    setPendingDelete(null);
  }, []);

  function createChat(connection = getNewChatAgent(agents)) {
    const chat = newChat(connection);
    setChats((cs) => [chat, ...cs]);
    setActiveId(chat.id);
    setView('chat');
    setMenuOpen(false);
    setPendingDelete(null);
  }

  const patchChat = useCallback((id, fn) => {
    setChats((cs) => cs.map((c) => (c.id === id ? fn(c) : c)));
  }, []);

  const deleteChat = useCallback((id) => {
    // Removes the local conversation only; traces, tests and projects
    // are evidence and stay.
    setPendingDelete(null);
    const next = chatsRef.current.filter((c) => c.id !== id);
    if (next.length) {
      setChats(next);
    } else {
      // Never strand the app with zero chats: with no active chat the
      // main area falls through to the dashboard.
      const fresh = newChat(getNewChatAgent(agents));
      setChats([fresh]);
      setActiveId(fresh.id);
    }
  }, [agents]);

  function openAgentManager() {
    setChangingAgent(false);
    setReconnectingAgentId(null);
    setView('agents');
    setMenuOpen(false);
  }

  function reconnectAgent(agentId) {
    if (!agents.some((agent) => agent.id === agentId)) return;
    setReconnectingAgentId(agentId);
    setChangingAgent(true);
    setConnectNotice(null);
    setView('agents');
    setMenuOpen(false);
  }

  const credentialInvalid = useCallback((credentialId) => {
    setAgents((current) => invalidateCredential(current, credentialId));
  }, []);

  function connectAgent(connection, opts = {}) {
    if (reconnectingAgentId && connection.projectId !== reconnectingAgentId) return;
    const wasFirst = agents.length === 0;
    const added = addAgent(agents, connection);
    const nextAgents = !reconnectingAgentId && opts.setAsDefault ? setDefault(added, connection.projectId) : added;
    const savedAgent = nextAgents.find((a) => a.id === connection.projectId);
    if (!savedAgent) return;
    setAgents(nextAgents);
    if (reconnectingAgentId) {
      setChats((cs) => refreshAgentChats(cs, savedAgent));
      setConnectNotice('Reconnected.');
      setChangingAgent(false);
      setReconnectingAgentId(null);
      return;
    }
    setConnectNotice(wasFirst ? `Connected. ${DEFAULT_DONE_NOTE}` : 'Connected.');
    const chat = activeChat && !activeChat.connection && !activeChat.messages.length && !activeChat.titleSet
      ? activeChat
      : newChat(null);
    setChats((cs) => [
      { ...chat, connection: { ...connection, ...savedAgent }, agentId: savedAgent.id, connectionDismissed: false },
      ...cs.filter((c) => c.id !== chat.id).map((c) => restoreChatConnection(c, nextAgents)),
    ]);
    setActiveId(chat.id);
    setChangingAgent(false);
    setView('agents');
    setMenuOpen(false);
  }

  function setDefaultAgent(agentId) {
    setConnectNotice(null);
    setAgents((as) => setDefault(as, agentId));
  }

  function deleteAgent(agentId) {
    const { agents: next, removed } = removeAgent(agents, agentId);
    if (!removed) return;
    setConnectNotice(null);
    setAgents(next);
    setChats((cs) => repointChats(cs, agentId, getDefaultAgent(next)));
  }

  function openEvidence({ projectId, runId, resultId }) {
    patchDash({
      tab: 'runs',
      projectId: projectId ?? getAgentForChat(agents, activeChat)?.projectId ?? activeChat?.connection?.projectId ?? dash.projectId,
      runId: runId ?? null,
      resultId: resultId ?? null,
      runVersion: 'all',
      resultFilter: 'all',
      splitFilter: 'all',
      traceVersion: 'all',
      traceStatus: 'all',
      traceId: null,
      spanId: null,
      traceVerdict: null,
      review: null,
      compareA: null,
      compareB: null,
    });
    setView('dashboard');
  }

  function openReview(review) {
    patchDash({ tab: 'improvements', review });
    setView('dashboard');
  }

  function continueInChat() {
    if (activeChat) openChat(activeChat.id);
    else createChat();
  }

  const groups = useMemo(
    () => groupChats(visibleChats(chats, chatsExpanded), groupMode),
    [chats, chatsExpanded, groupMode],
  );

  return (
    <div className="app">
      <button
        type="button"
        className="menu-btn"
        aria-label="Toggle sidebar"
        onClick={() => setMenuOpen((v) => !v)}
      >
        ☰
      </button>
      <aside className={`sidebar${menuOpen ? ' open' : ''}`}>
        <div className="brand-link">
          <LogoMark />
          <span>AgentLab</span>
        </div>
        {connectedAgents.length ? (
          <button
            type="button"
            className="new-chat"
            onClick={() => createChat()}
            onPointerEnter={() => preloadView(loadChatView)}
            onFocus={() => preloadView(loadChatView)}
          >
            <span aria-hidden="true">＋</span> New chat
          </button>
        ) : null}
        <nav aria-label="Recent chats">
          <div className="sidebar-heading-row">
            <p className="sidebar-heading">Recent chats</p>
            <div className="group-toggle" role="group" aria-label="Group chats by">
              <button
                type="button"
                aria-pressed={groupMode === 'date'}
                onClick={() => setGroupMode('date')}
              >
                Date
              </button>
              <button
                type="button"
                aria-pressed={groupMode === 'agent'}
                onClick={() => setGroupMode('agent')}
              >
                Agent
              </button>
            </div>
          </div>
          {groups.map((group) => (
            <div key={group.key} className="chat-group">
              <p className="chat-group-heading">{group.label}</p>
              <ul>
                {group.chats.map((c) => (
                  <ChatRow
                    key={c.id}
                    id={c.id}
                    title={c.title}
                    selected={view === 'chat' && activeChat?.id === c.id}
                    armed={pendingDelete === c.id}
                    deletable={chats.length > 1 || (c.messages?.length ?? 0) > 0 || !!c.titleSet}
                    onOpen={openChat}
                    onArm={setPendingDelete}
                    onConfirm={deleteChat}
                  />
                ))}
              </ul>
            </div>
          ))}
          {chats.length > RECENT_LIMIT && (
            <button
              type="button"
              className="see-more"
              aria-expanded={chatsExpanded}
              onClick={() => {
                setChatsExpanded((v) => !v);
                setPendingDelete(null);
              }}
            >
              {chatsExpanded ? 'Show less' : `See more (${chats.length - RECENT_LIMIT} more)`}
            </button>
          )}
        </nav>
        <hr className="sidebar-divider" />
        <button
          type="button"
          className={view === 'dashboard' ? 'selected' : ''}
          onPointerEnter={() => preloadView(loadDashboardView)}
          onFocus={() => preloadView(loadDashboardView)}
          onClick={() => {
            setView('dashboard');
            setMenuOpen(false);
          }}
        >
          Dashboard
        </button>
        <div className="sidebar-agent">
          <button
            type="button"
            className={`agent-manager-button${view === 'agents' ? ' selected' : ''}`}
            onPointerEnter={() => preloadView(loadConnectForm)}
            onFocus={() => preloadView(loadConnectForm)}
            onClick={openAgentManager}
          >
            <span className={`connection-dot${connectedAgents.length ? ' connected' : ''}`} aria-hidden="true" />
            <span className="agent-manager-text">
              Agent Manager
              <span className="connection-label truncate" title={connectedAgents[0]?.model}>
                {connectedAgents.length > 1
                  ? `Connected · ${connectedAgents.length} agents`
                  : connectedAgents.length
                    ? `Connected · ${connectedAgents[0].model}`
                    : 'Not connected'}
              </span>
            </span>
          </button>
        </div>
        <div className="theme-toggle" role="group" aria-label="Color theme">
          <span
            className="theme-thumb"
            aria-hidden="true"
            style={{ transform: `translateX(${THEME_OPTIONS.indexOf(theme) * 100}%)` }}
          />
          {THEME_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={theme === option}
              aria-label={`${option[0].toUpperCase() + option.slice(1)} theme`}
              title={`${option[0].toUpperCase() + option.slice(1)} theme`}
              onClick={() => setTheme(option)}
            >
              <ThemeIcon name={option} />
            </button>
          ))}
        </div>
      </aside>
      <main className="main">
        <Suspense fallback={<Spinner />}>
        {view === 'agents' ? (
          <div>
            <header className="page-head">
              <h1>Agent Manager</h1>
              <span className={connectedAgents.length ? 'good-text' : 'bad-text'} role="status">
                {connectedAgents.length ? 'Connected' : 'Not connected'}
              </span>
            </header>
            <div className="chat-narrow agents-narrow">
              {connectNotice ? (
                <p className="good-text" role="status">
                  {connectNotice}
                </p>
              ) : null}
              {agents.length && !changingAgent ? (
                <>
                  <ul className="agent-list">
                    {agents.map((agent) => (
                      <li key={agent.projectId}>
                        <section className="card connected-agent" aria-label={agent.name || agent.model}>
                          <h2>{agent.name || 'Saved agent'}</h2>
                          <dl className="agent-details">
                            <dt>Status</dt>
                            <dd className={isConnected(agent, nowMs) ? 'good-text' : 'bad-text'}>
                              {isConnected(agent, nowMs) ? 'Connected' : 'Not connected'}
                              {agent.isDefault ? ' · Default' : ''}
                            </dd>
                            <dt>Model</dt>
                            <dd>{agent.model}</dd>
                            <dt>Endpoint</dt>
                            <dd>{agent.endpoint}</dd>
                          </dl>
                          <div className="connect-actions">
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={() => reconnectAgent(agent.id)}
                            >
                              Reconnect
                            </button>
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={() => createChat(agent)}
                              disabled={!isConnected(agent, nowMs)}
                              title={isConnected(agent, nowMs) ? undefined : 'Reconnect this agent first'}
                            >
                              New chat with this agent
                            </button>
                            {!agent.isDefault && isConnected(agent, nowMs) ? (
                              <button
                                type="button"
                                className="btn-secondary"
                                onClick={() => setDefaultAgent(agent.id)}
                              >
                                Set as default
                              </button>
                            ) : null}
                            {agent.isDefault ? (
                              <button
                                type="button"
                                className="btn-secondary"
                                disabled
                                title="The default agent can't be deleted"
                              >
                                Delete
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="btn-secondary"
                                onClick={() => deleteAgent(agent.id)}
                              >
                                Delete
                              </button>
                            )}
                          </div>
                        </section>
                      </li>
                    ))}
                  </ul>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => {
                      setConnectNotice(null);
                      setReconnectingAgentId(null);
                      setChangingAgent(true);
                    }}
                  >
                    Connect another agent
                  </button>
                </>
              ) : (
                <>
                  <p className="muted">{reconnectingAgent ? 'Reconnect this agent to continue using its existing chats.' : 'Connect an agent here to use it in new chats.'}</p>
                  <ConnectForm
                    key={reconnectingAgentId ?? 'new-agent'}
                    agents={agents}
                    reconnectAgent={reconnectingAgent}
                    onConnect={connectAgent}
                    onCancel={agents.length ? () => {
                      setChangingAgent(false);
                      setReconnectingAgentId(null);
                    } : undefined}
                  />
                </>
              )}
            </div>
          </div>
        ) : view === 'chat' && activeChat ? (
          <ChatView
            key={activeChat.id}
            chat={activeChat}
            agents={agents}
            nowMs={nowMs}
            agentKeys={agentKeys}
            onPatch={patchChat}
            onOpenAgentManager={openAgentManager}
            onReconnectAgent={reconnectAgent}
            onCredentialInvalid={credentialInvalid}
            onOpenEvidence={openEvidence}
            onOpenReview={openReview}
          />
        ) : (
          <DashboardView
            dash={dash}
            patchDash={patchDash}
            chatCredentialId={chatCredentialId}
            onContinueInChat={continueInChat}
            onOpenAgentManager={openAgentManager}
          />
        )}
        </Suspense>
      </main>
    </div>
  );
}
