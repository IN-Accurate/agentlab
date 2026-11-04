import * as AvatarPrimitive from '@radix-ui/react-avatar';

// shadcn/ui Avatar composition, styled with the application's CSS tokens.
// https://ui.shadcn.com/docs/components/radix/avatar
export function Avatar({ className = '', ...props }) {
  return <AvatarPrimitive.Root className={`avatar ${className}`} {...props} />;
}

export function AvatarFallback({ className = '', ...props }) {
  return <AvatarPrimitive.Fallback className={`avatar-fallback ${className}`} {...props} />;
}
