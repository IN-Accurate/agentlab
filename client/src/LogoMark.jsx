import { useId } from 'react';

// Inline copy of the shapes in client/src/assets/logo-mark.svg (kept in sync
// by hand). Inlined so the theme's CSS variables reach the gradient stops.
// Decorative: the wordmark next to it carries the accessible name.
export default function LogoMark() {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '');
  const grad = `logo-grad-${uid}`;
  return (
    <svg viewBox="0 0 100 84" className="logo-mark" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={grad} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="84">
          <stop offset="0" style={{ stopColor: 'var(--logo-grad-start, #7B818B)' }} />
          <stop offset="1" style={{ stopColor: 'var(--logo-grad-end, #2B2F36)' }} />
        </linearGradient>
      </defs>
      <path
        d="M12.5 71.5 L50 12.5 L87.5 71.5"
        fill="none"
        stroke={`url(#${grad})`}
        strokeWidth="8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M12.5 71.5 C25 62 38 59 50 59 C62 59 75 62 87.5 71.5"
        fill="none"
        stroke={`url(#${grad})`}
        strokeWidth="5"
        strokeLinecap="round"
      />
      <g fill={`url(#${grad})`}>
        <circle cx="50" cy="12.5" r="12.5" />
        <circle cx="12.5" cy="71.5" r="12.5" />
        <circle cx="87.5" cy="71.5" r="12.5" />
        <circle cx="50" cy="59" r="9.5" fillOpacity="0.85" />
      </g>
    </svg>
  );
}
