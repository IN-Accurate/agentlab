import { memo, useMemo, useState } from 'react';
import { Modal } from '../ui.jsx';
import Markdown from './Markdown.jsx';
import { remarkResponsePreview, responseExcerpt } from './responsePreview.js';
import './responsePreview.css';

const previewPlugins = [remarkResponsePreview];

export default memo(function ResponsePreview({ children, className = '', title = 'Full agent response', action }) {
  const text = typeof children === 'string' ? children : '';
  const [open, setOpen] = useState(false);
  const { truncated } = useMemo(() => responseExcerpt(text), [text]);

  if (!truncated && !open) return <Markdown className={className}>{text}</Markdown>;

  return (
    <div className={`response-preview ${className}`}>
      <div className="response-preview-content">
        <Markdown remarkPlugins={truncated ? previewPlugins : undefined}>{text}</Markdown>
      </div>
      <div className="response-preview-footer">
        {truncated ? <span className="muted" aria-hidden="true">…</span> : null}
        <button type="button" className="link response-preview-open" onClick={() => setOpen(true)}>
          View full response
        </button>
      </div>
      {open ? (
        <Modal title={title} className="response-modal" onClose={() => setOpen(false)}>
          <div className="response-modal-body"><Markdown>{text}</Markdown></div>
          {action ? <div className="response-modal-actions">{action}</div> : null}
        </Modal>
      ) : null}
    </div>
  );
});
