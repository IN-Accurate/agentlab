// Kept apart from ui.jsx so the dialog code (Radix) loads only with the views
// that open dialogs, not with the app shell.
import { useEffect, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { cn } from '@/lib/utils';

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
