/**
 * The application shell: header, status, and the region a screen renders into.
 */

import type { ReactNode } from 'react';

import { StatusDot } from './primitives';

export type ConnectionState = 'ok' | 'degraded' | 'down' | 'unknown';

export function Layout({
  connection,
  children,
}: {
  connection: ConnectionState;
  children: ReactNode;
}) {
  return (
    <div className="shell">
      <header className="shell__header">
        <div className="shell__brand">
          <span className="shell__mark" aria-hidden="true" />
          <span className="shell__name">NexaAssist</span>
        </div>
        <StatusDot status={connection} />
      </header>
      {/* One landmark for the whole screen, so keyboard users can skip to it. */}
      <main className="shell__main" id="main">
        {children}
      </main>
    </div>
  );
}
