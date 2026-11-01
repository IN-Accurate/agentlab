import { useCallback, useEffect, useState } from 'react';
import ChatView from './chat/ChatView.jsx';
import ConnectForm from './chat/ConnectForm.jsx';
import DashboardView from './dashboard/DashboardView.jsx';

const STORE_KEY = 'testlab.chats.v1';

function loadChats() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return [];
    return JSON.parse(raw).map((c) => ({
      ...c,
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

function newChat(connection) {
  return {
    id: `c${Date.now()}`,
    title: 'New chat',
    titleSet: false,
    connection: connection ? { ...connection, ownProject: false } : null,
    messages: [],
    streaming: false,
  };
}

export default function App() {
  const [chats, setChats] = useState(() => {
    const loaded = loadChats();
    return loaded.length ? loaded : [newChat(null)];
  });
  const [activeId, setActiveId] = useState(null);
  const [view, setView] = useState('chat');
  const [dash, setDash] = useState({ tab: 'overview', projectId: null });
  const [menuOpen, setMenuOpen] = useState(false);
  const [changingAgent, setChangingAgent] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(chats));
    } catch {
      // Private browsing: conversations last for this tab only.
    }
  }, [chats]);

  const activeChat = (activeId && chats.find((c) => c.id === activeId)) || chats[0] || null;
  const lastConnection = chats.find((c) => c.connection)?.connection ?? null;
  const chatCredentialId =
    chats.find((c) => c.connection?.projectId === dash.projectId)?.connection
      ?.credentialId ?? null;

  const patchDash = useCallback((patch) => {
    setDash((d) => ({ ...d, ...patch }));
  }, []);

  function openChat(id) {
    setActiveId(id);
    setView('chat');
    setMenuOpen(false);
  }

  function createChat() {
    const chat = newChat(lastConnection);
    setChats((cs) => [chat, ...cs]);
    setActiveId(chat.id);
    setView('chat');
    setMenuOpen(false);
  }

  function patchChat(id, fn) {
    setChats((cs) => cs.map((c) => (c.id === id ? fn(c) : c)));
  }

  function openAgentManager() {
    setChangingAgent(false);
    setView('agents');
    setMenuOpen(false);
  }

  function connectAgent(connection) {
    const chat = activeChat && !activeChat.connection ? activeChat : newChat(null);
    setChats((cs) => [
      { ...chat, connection },
      ...cs.filter((c) => c.id !== chat.id).map((c) =>
        c.connection ? c : { ...c, connection: { ...connection, ownProject: false } },
      ),
    ]);
    setActiveId(chat.id);
    setChangingAgent(false);
    setView('agents');
    setMenuOpen(false);
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
        <div className="brand">TestLab</div>
        <button type="button" className="new-chat" onClick={createChat}>
          <span aria-hidden="true">＋</span> New chat
        </button>
        <nav aria-label="Recent chats">
          <p className="sidebar-heading">Recent chats</p>
          <ul>
            {chats.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className={view === 'chat' && activeChat?.id === c.id ? 'selected' : ''}
                  onClick={() => openChat(c.id)}
                  title={c.title}
                >
                  {c.title}
                </button>
              </li>
            ))}
          </ul>
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
            <span className={`connection-dot${lastConnection ? ' connected' : ''}`} aria-hidden="true" />
            <span className="agent-manager-text">
              Agent Manager
              <span className="connection-label truncate" title={lastConnection?.model}>
                {lastConnection ? `Connected · ${lastConnection.model}` : 'Not connected'}
              </span>
            </span>
          </button>
        </div>
      </aside>
      <main className="main">
        {view === 'agents' ? (
          <div>
            <header className="page-head">
              <h1>Agent Manager</h1>
              <span className={lastConnection ? 'good-text' : 'bad-text'} role="status">
                {lastConnection ? 'Agent connected' : 'Not connected'}
              </span>
            </header>
            <div className="chat-narrow">
              {lastConnection && !changingAgent ? (
                <section className="card connected-agent" aria-label="Connected agent">
                  <h2>Connected agent</h2>
                  <dl className="agent-details">
                    <dt>Model</dt>
                    <dd>{lastConnection.model}</dd>
                    <dt>Endpoint</dt>
                    <dd>{lastConnection.endpoint}</dd>
                  </dl>
                  <button type="button" className="btn-secondary" onClick={() => setChangingAgent(true)}>
                    Change agent
                  </button>
                </section>
              ) : (
                <>
                  <p className="muted">Connect an agent here to use it in new chats.</p>
                  <ConnectForm
                    initialEndpoint={lastConnection?.endpoint ?? ''}
                    initialModel={lastConnection?.model ?? ''}
                    onConnect={connectAgent}
                    onCancel={lastConnection ? () => setChangingAgent(false) : undefined}
                  />
                </>
              )}
            </div>
          </div>
        ) : view === 'chat' && activeChat ? (
          <ChatView
            chat={activeChat}
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
          />
        )}
      </main>
    </div>
  );
}
