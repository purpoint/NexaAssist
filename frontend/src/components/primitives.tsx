/**
 * Small building blocks shared by every screen.
 *
 * Deliberately few: a component library is worth having once there is enough
 * UI to be inconsistent about, and inventing one before then is guessing at
 * what the product needs.
 */

import type { ReactNode } from 'react';

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <span className="spinner" role="status" aria-live="polite">
      <span className="spinner__dot" aria-hidden="true" />
      <span className="visually-hidden">{label}</span>
    </span>
  );
}

export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    // `alert` so a screen reader announces it without the user hunting for it.
    <div className="banner banner--error" role="alert">
      <span>{message}</span>
      {onRetry ? (
        <button type="button" className="button button--quiet" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {children ? <p className="empty__body">{children}</p> : null}
    </div>
  );
}

export function StatusDot({ status }: { status: 'ok' | 'degraded' | 'down' | 'unknown' }) {
  const labels: Record<typeof status, string> = {
    ok: 'Connected',
    degraded: 'Degraded',
    down: 'Unavailable',
    unknown: 'Checking',
  };
  return (
    <span className={`status status--${status}`}>
      <span className="status__dot" aria-hidden="true" />
      {/* The label is text, not colour alone: colour is not available to
          everyone and is not available at all to a screen reader. */}
      <span className="status__label">{labels[status]}</span>
    </span>
  );
}
