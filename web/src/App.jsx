import { useState } from 'react';

export default function App() {
  const [view, setView] = useState('chat');

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">TestLab</div>
        <button type="button" className="new-chat">
          <span aria-hidden="true">＋</span> New chat
        </button>
        <nav aria-label="Recent chats">
          <p className="sidebar-heading">Recent chats</p>
          <ul>
            <li>
              <button
                type="button"
                className={view === 'chat' ? 'selected' : ''}
                onClick={() => setView('chat')}
              >
                New chat
              </button>
            </li>
          </ul>
        </nav>
        <hr className="sidebar-divider" />
        <button
          type="button"
          className={view === 'dashboard' ? 'selected' : ''}
          onClick={() => setView('dashboard')}
        >
          Dashboard
        </button>
      </aside>
      <main className="content">
        {view === 'chat' ? <p>Chat workspace</p> : <p>Dashboard</p>}
      </main>
    </div>
  );
}
