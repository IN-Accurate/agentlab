import { useState } from 'react';

export default function Composer({ disabled, placeholder, onSend }) {
  const [text, setText] = useState('');
  const [context, setContext] = useState('');
  const [showContext, setShowContext] = useState(false);
  const sendable = text.trim().length > 0 && !disabled;

  function submit() {
    if (!sendable) return;
    onSend(text.trim(), context.trim());
    setText('');
    setContext('');
    setShowContext(false);
  }

  return (
    <div className="composer">
      {showContext && !disabled ? (
        <textarea
          className="composer-context"
          value={context}
          placeholder="Optional policy or context to include…"
          aria-label="Optional policy or context"
          rows={2}
          onChange={(e) => setContext(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
      ) : null}
      <textarea
        value={text}
        placeholder={placeholder}
        aria-label="Message"
        rows={2}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer-row">
        <button
          type="button"
          className="btn-ghost"
          aria-label="Add policy or context"
          disabled={disabled}
          onClick={() => setShowContext((v) => !v)}
        >
          +
        </button>
        <button
          type="button"
          className="btn-send"
          aria-label="Send"
          disabled={!sendable}
          onClick={submit}
        >
          ↑
        </button>
      </div>
    </div>
  );
}
