/**
 * Small building blocks shared by every screen.
 *
 * Deliberately few: a component library is worth having once there is enough
 * UI to be inconsistent about, and inventing one before then is guessing at
 * what the product needs.
 *
 * ErrorBanner and EmptyState lived here until the screens that used them
 * grew their own: failures are now categorised by ErrorNotice, and the empty
 * conversation is the Welcome screen. Keeping unused exports around is how a
 * component library ends up with three ways to say the same thing.
 */

import type { RealtimeState } from '../realtime/useRealtime';

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <span className="spinner" role="status" aria-live="polite">
      <span className="spinner__dot" aria-hidden="true" />
      <span className="visually-hidden">{label}</span>
    </span>
  );
}

export function StatusDot({ status }: { status: 'ok' | 'degraded' | 'down' | 'unknown' }) {
  const labels: Record<typeof status, string> = {
    ok: 'Operational',
    degraded: 'Degraded',
    down: 'Unavailable',
    unknown: 'Checking',
  };
  return (
    // The name is on the element, so the label may collapse to a dot on a
    // narrow screen without the status becoming colour-only.
    <span
      className={`status status--${status}`}
      title={`Backend: ${labels[status]}`}
      aria-label={`Backend ${labels[status]}`}
    >
      <span className="status__dot" aria-hidden="true" />
      {/* The label is text, not colour alone: colour is not available to
          everyone and is not available at all to a screen reader. */}
      <span className="status__label">{labels[status]}</span>
    </span>
  );
}

/**
 * Whether realtime streaming is available.
 *
 * A separate question from backend readiness, and separately displayed: an
 * unavailable socket does not stop the assistant answering, it only stops the
 * answer arriving a piece at a time.
 */
export function RealtimeStatus({ state }: { state: RealtimeState }) {
  const labels: Record<RealtimeState, string> = {
    connecting: 'Connecting…',
    open: 'Live',
    reconnecting: 'Reconnecting…',
    unavailable: 'Offline',
  };
  const tone: Record<RealtimeState, string> = {
    connecting: 'connecting',
    open: 'ok',
    reconnecting: 'degraded',
    unavailable: 'down',
  };
  return (
    <span
      className={`status status--${tone[state]}`}
      title={`Realtime: ${labels[state]}`}
      aria-label={`Realtime ${labels[state]}`}
      // Announced when it changes, because losing the live connection is
      // something a user should learn without watching the corner.
      role="status"
      aria-live="polite"
    >
      <span className="status__dot" aria-hidden="true" />
      <span className="status__label">{labels[state]}</span>
    </span>
  );
}
