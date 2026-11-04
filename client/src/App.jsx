import { Suspense, lazy, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DEFAULT_DONE_NOTE,
  addAgent,
  getDefaultAgent,
  getNewChatAgent,
  isConnected,
  loadAgentStore,
  removeAgent,
  repointChats,
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

const ChatView = lazy(() => import('./chat/ChatView.jsx'));
const ConnectForm = lazy(() => import('./chat/ConnectForm.jsx'));
const DashboardView = lazy(() => import('./dashboard/DashboardView.jsx'));

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
          endpoint: connection.endpoint,
          model: connection.model,
          ownProject: false,
        }
      : null,
    agentId: connection?.projectId ?? null,
    messages: [],
    streaming: false,
  };
}

export default function App() {
  const [chats, setChats] = useState(() => {
    const loaded = loadChats();
    return loaded.length ? loaded : [newChat(null)];
  });
  // One entry per project, newest first; each keeps the key it connected with.
  const [agents, setAgents] = useState(() => loadAgentStore(localStorage));
  const [savedUi] = useState(loadUi);
  const [connectNotice, setConnectNotice] = useState(null);
  const [activeId, setActiveId] = useState(savedUi.activeId);
  const [view, setView] = useState(savedUi.view);
  const [dash, setDash] = useState(savedUi.dash);
  const [menuOpen, setMenuOpen] = useState(false);
  const [changingAgent, setChangingAgent] = useState(false);
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

  const activeChat = (activeId && chats.find((c) => c.id === activeId)) || chats[0] || null;
  const connectedAgents = agents.filter((a) => isConnected(a, nowMs));
  const agentKeys = Object.fromEntries(agents.map((a) => [a.projectId, a.credentialId]));
  const chatCredentialId =
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

  function patchChat(id, fn) {
    setChats((cs) => cs.map((c) => (c.id === id ? fn(c) : c)));
  }

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
      const fresh = newChat(null);
      setChats([fresh]);
      setActiveId(fresh.id);
    }
  }, []);

  function openAgentManager() {
    setChangingAgent(false);
    setView('agents');
    setMenuOpen(false);
  }

  function connectAgent(connection, opts = {}) {
    const wasFirst = agents.length === 0;
    setAgents((as) => {
      const added = addAgent(as, connection);
      return opts.setAsDefault ? setDefault(added, connection.projectId) : added;
    });
    setConnectNotice(wasFirst ? `Connected. ${DEFAULT_DONE_NOTE}` : 'Connected.');
    const chat = activeChat && !activeChat.connection ? activeChat : newChat(null);
    setChats((cs) => [
      { ...chat, connection, agentId: connection.projectId },
      ...cs.filter((c) => c.id !== chat.id).map((c) =>
        c.connection
          ? c
          : {
              ...c,
              connection: { ...connection, ownProject: false },
              agentId: connection.projectId,
            },
      ),
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

  function openEvidence({ runId, resultId }) {
    patchDash({ tab: 'runs', runId: runId ?? null, resultId: resultId ?? null });
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
          <button type="button" className="new-chat" onClick={createChat}>
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
                          <h2>{agent.name || 'Connected agent'}</h2>
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
                              onClick={() => createChat(agent)}
                              disabled={!connectedAgents.length}
                              title={connectedAgents.length ? undefined : 'Connect an agent first'}
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
                      setChangingAgent(true);
                    }}
                  >
                    Connect another agent
                  </button>
                </>
              ) : (
                <>
                  <p className="muted">Connect an agent here to use it in new chats.</p>
                  <ConnectForm
                    agents={agents}
                    onConnect={connectAgent}
                    onCancel={agents.length ? () => setChangingAgent(false) : undefined}
                  />
                </>
              )}
            </div>
          </div>
        ) : view === 'chat' && activeChat ? (
          <ChatView
            chat={activeChat}
            agents={agents}
            nowMs={nowMs}
            agentKeys={agentKeys}
            onPatch={patchChat}
            onOpenAgentManager={openAgentManager}
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
