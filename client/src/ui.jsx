import { useEffect, useRef } from 'react';

export function Spinner({ label = 'Loading…' }) {
  return (
    <p className="muted" role="status">
      {label}
    </p>
  );
}

export function Empty({ title, hint, action }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {hint ? <p className="muted">{hint}</p> : null}
      {action ?? null}
    </div>
  );
}

export function ErrorBox({ error, onRetry }) {
  return (
    <div className="error-box" role="alert">
      <p>{error?.message ?? 'Something went wrong.'}</p>
      {onRetry ? (
        <button type="button" className="btn-secondary" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

const TONES = {
  Passed: 'good',
  pass: 'good',
  Accepted: 'good',
  ok: 'good',
  Failed: 'bad',
  fail: 'bad',
  Rejected: 'bad',
  error: 'bad',
  Skipped: 'neutral',
  skipped: 'neutral',
};

export function Verdict({ value }) {
  if (value === null || value === undefined || value === 'unavailable')
    return <span className="verdict neutral">Unavailable</span>;
  const label = typeof value === 'boolean' ? (value ? 'Passed' : 'Failed') : value;
  return <span className={`verdict ${TONES[label] ?? 'neutral'}`}>{label}</span>;
}

export function Field({ label, error, children }) {
  const id = `field-${label.replace(/[^a-z]+/gi, '-').toLowerCase()}`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

const FOCUSABLE =
  'a[href], area[href], button, input, select, textarea, summary, audio[controls], video[controls], iframe, [contenteditable], [tabindex]';

export function Modal({ title, onClose, children }) {
  const dialog = useRef(null);

  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.focus();
    return () => previous?.focus?.();
  }, []);

  function handleKey(event) {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const items = [...dialog.current.querySelectorAll(FOCUSABLE)].filter(
      (item) =>
        item.tabIndex >= 0 &&
        !item.matches(':disabled') &&
        item.getClientRects().length > 0 &&
        getComputedStyle(item).visibility === 'visible',
    );
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (!dialog.current.contains(active)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (event.shiftKey && (active === first || active === dialog.current)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        ref={dialog}
        tabIndex={-1}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={handleKey}
      >
        <div className="modal-head">
          <h2>{title}</h2>
          <button type="button" className="btn-ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function fmtPct(value, digits = 1) {
  if (value === null || value === undefined) return 'unavailable';
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay)
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export function shortId(id) {
  return String(id ?? '').slice(0, 8);
}
