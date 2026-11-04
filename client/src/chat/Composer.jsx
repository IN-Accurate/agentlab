import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowUp, Plus, X } from 'lucide-react';
import { Button } from '@/components/ui/button';

function sizeToContent(textarea) {
  if (!textarea) return;
  const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
  const maxHeight = Math.max(24, Math.min(160, viewportHeight * 0.25));
  // Reset before measuring so deleting text can shrink the field as well.
  textarea.style.height = 'auto';
  const contentHeight = textarea.scrollHeight + textarea.offsetHeight - textarea.clientHeight;
  textarea.style.height = `${Math.min(contentHeight, maxHeight)}px`;
  textarea.style.overflowY = contentHeight > maxHeight ? 'auto' : 'hidden';
}

export default function Composer({ disabled, placeholder, onSend }) {
  const [text, setText] = useState('');
  const [context, setContext] = useState('');
  const [showContext, setShowContext] = useState(false);
  const messageRef = useRef(null);
  const contextRef = useRef(null);
  const sendable = text.trim().length > 0 && !disabled;
  const resize = useCallback(() => {
    sizeToContent(messageRef.current);
    sizeToContent(contextRef.current);
  }, []);

  useLayoutEffect(resize, [resize, text, context, showContext, disabled]);
  useEffect(() => {
    const viewport = window.visualViewport;
    window.addEventListener('resize', resize);
    viewport?.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      viewport?.removeEventListener('resize', resize);
    };
  }, [resize]);

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
          ref={contextRef}
          className="composer-context"
          value={context}
          placeholder="Optional policy or context to include…"
          aria-label="Optional policy or context"
          rows={1}
          onChange={(e) => setContext(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229) {
              e.preventDefault();
              submit();
            }
          }}
        />
      ) : null}
      <textarea
        ref={messageRef}
        value={text}
        placeholder={placeholder}
        aria-label="Message"
        rows={1}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer-row">
        <Button
          type="button"
          variant="ghost" size="icon-sm"
          aria-label={showContext ? 'Hide policy or context' : 'Add policy or context'}
          aria-expanded={showContext}
          disabled={disabled}
          onClick={() => setShowContext((v) => !v)}
        >
          {showContext ? <X size={17} aria-hidden="true" /> : <Plus size={17} aria-hidden="true" />}
        </Button>
        <button
          type="button"
          className="btn-send"
          aria-label="Send"
          disabled={!sendable}
          onClick={submit}
        >
          <ArrowUp size={18} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
