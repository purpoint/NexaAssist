/**
 * The application shell: header, status, and the region a screen renders into.
 */

import type { ReactNode } from 'react';

import { StatusDot } from './primitives';

export type ConnectionState = 'ok' | 'degraded' | 'down' | 'unknown';

export function Layout({
  connection,
  keyConfigured,
  onManageKey,
  children,
}: {
  connection: ConnectionState;
  keyConfigured: boolean;
  onManageKey: () => void;
  children: ReactNode;
}) {
  return (
    <div className="shell">
      <header className="shell__header">
        <div className="shell__brand">
          <span className="shell__mark" aria-hidden="true" />
          <span className="shell__name">NexaAssist</span>
        </div>
        <div className="shell__controls">
          <StatusDot status={connection} />
          <button
            type="button"
            className="button button--quiet shell__key"
            onClick={onManageKey}
            // The label says which state it is in, because a key icon alone
            // cannot distinguish "set" from "not set".
            aria-label={keyConfigured ? 'API key set — manage it' : 'Add an API key'}
          >
            <span className={`key-dot key-dot--${keyConfigured ? 'set' : 'unset'}`} aria-hidden="true" />
            {keyConfigured ? 'Key set' : 'Add key'}
          </button>
        </div>
      </header>
      {/* One landmark for the whole screen, so keyboard users can skip to it. */}
      <main className="shell__main" id="main">
        {children}
      </main>
    </div>
  );
}
