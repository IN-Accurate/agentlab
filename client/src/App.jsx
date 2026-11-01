import { useCallback, useEffect, useState } from 'react';
import ChatView from './chat/ChatView.jsx';
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

  function connectChat(id, connection) {
    patchChat(id, (c) => ({ ...c, connection }));
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
      </aside>
      <main className="main">
        {view === 'chat' && activeChat ? (
          <ChatView
            chat={activeChat}
            lastConnection={lastConnection}
            onPatch={patchChat}
            onConnect={connectChat}
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
