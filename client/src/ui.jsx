import { useEffect, useState } from 'react';
import { CircleAlert, LoaderCircle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

export function Spinner({ label = 'Loading…' }) {
  return (
    <span
      className="loading-status inline-flex items-center gap-2 text-[13px] text-muted-foreground"
      role="status"
      aria-live="polite"
    >
      <LoaderCircle className="size-4 shrink-0 animate-spin" aria-hidden="true" />
      <span>{label}</span>
    </span>
  );
}

export function Empty({ title, hint, action }) {
  return (
    <div className="empty flex flex-col items-start gap-1 py-8">
      <p className="m-0 text-[17px] font-semibold">{title}</p>
      {hint ? <p className="muted m-0 text-sm text-muted-foreground">{hint}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

export function ErrorBox({ error, onRetry }) {
  return (
    <Alert variant="destructive" className="error-box my-3 border-failure/30 bg-failure-soft">
      <CircleAlert aria-hidden="true" />
      <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
        <p className="m-0">{error?.message ?? 'Something went wrong.'}</p>
        {onRetry ? (
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

const TONES = {
  Passed: 'success',
  pass: 'success',
  Accepted: 'success',
  ok: 'success',
  Failed: 'failure',
  fail: 'failure',
  Rejected: 'failure',
  error: 'failure',
  Skipped: 'secondary',
  skipped: 'secondary',
};

export function Verdict({ value }) {
  if (value === null || value === undefined || value === 'unavailable')
    return (
      <Badge variant="outline" className="verdict text-muted-foreground">
        Unavailable
      </Badge>
    );
  const label = typeof value === 'boolean' ? (value ? 'Passed' : 'Failed') : value;
  return (
    <Badge variant={TONES[label] ?? 'outline'} className="verdict">
      {label}
    </Badge>
  );
}

export function Field({ label, error, children }) {
  const id = `field-${label.replace(/[^a-z]+/gi, '-').toLowerCase()}`;
  return (
    <div className="field my-3 grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children(id)}
      {error ? (
        <p className="m-0 text-sm text-failure" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** A shadcn (Radix) dialog: focus trap, Escape and outside click close it. */
export function Modal({ title, description, onClose, children, className = '' }) {
  // The app opens dialogs itself (no Radix trigger), so focus goes back by hand.
  const [opener] = useState(() => document.activeElement);
  useEffect(() => () => opener?.focus?.(), [opener]);
  return (
    <Dialog open onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent
        className={cn('max-h-[90dvh] overflow-y-auto sm:max-w-[440px]', className)}
        onCloseAutoFocus={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle className="font-heading text-[19px]">{title}</DialogTitle>
          {description ? (
            <DialogDescription>{description}</DialogDescription>
          ) : (
            <DialogDescription className="sr-only">{title}</DialogDescription>
          )}
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
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

/** A shadcn-styled segmented control: real buttons with aria-pressed, one selected. */
export function Segmented({ label, options, value, onChange, className = '' }) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn(
        'inline-flex h-9 w-fit shrink-0 items-center rounded-lg bg-muted p-[3px] text-muted-foreground',
        className,
      )}
    >
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          className={cn(
            'inline-flex h-full items-center justify-center rounded-md px-3 text-sm font-medium whitespace-nowrap transition-colors',
            'hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none',
            value === key && 'active bg-background text-foreground shadow-sm',
          )}
          onClick={() => onChange(key)}
        >
          {text}
        </button>
      ))}
    </div>
  );
}
