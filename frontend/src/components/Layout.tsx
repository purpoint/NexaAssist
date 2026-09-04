/**
 * The application shell: header, sidebar, and the region a screen renders into.
 *
 * Two status signals sit in the header and they are not the same question.
 * Readiness is "can the backend do its job", answered by polling /ready and
 * including whether its database and provider are configured. The socket
 * state is "is realtime available right now". Collapsing them into one light
 * would mean a green dot on a page that cannot stream, or a red one on a page
 * answering perfectly well over HTTP.
 */

import type { ReactNode } from 'react';

import type { RealtimeState } from '../realtime/useRealtime';
import { BrandMark, KeyIcon, MenuIcon } from './icons';
import { RealtimeStatus, StatusDot } from './primitives';

export type ConnectionState = 'ok' | 'degraded' | 'down' | 'unknown';

export function Layout({
  connection,
  realtime,
  keyConfigured,
  onManageKey,
  sidebar,
  sidebarOpen = false,
  onToggleSidebar,
  children,
}: {
  connection: ConnectionState;
  /** Omitted where there is no socket, e.g. a screen that does not stream. */
  realtime?: RealtimeState;
  keyConfigured: boolean;
  onManageKey: () => void;
  sidebar?: ReactNode;
  sidebarOpen?: boolean;
  onToggleSidebar?: () => void;
  children: ReactNode;
}) {
  return (
    <div className="shell">
      <header className="shell__header">
        <div className="shell__brand">
          {onToggleSidebar ? (
            <button
              type="button"
              className="button button--quiet button--small shell__menu-toggle"
              onClick={onToggleSidebar}
              aria-label={sidebarOpen ? 'Hide conversations' : 'Show conversations'}
              aria-expanded={sidebarOpen}
            >
              <MenuIcon />
            </button>
          ) : null}
          <span className="brand__mark">
            <BrandMark size={19} />
          </span>
          <span className="brand__text">
            <span className="brand__name">NexaAssist</span>
            {/* What the product is, said once, where a first-time viewer
                will look before anything else. */}
            <span className="brand__descriptor">AI Support Platform</span>
          </span>
        </div>

        <div className="shell__controls">
          {/* Grouped, because they answer one question between them -- is the
              product working -- and separated from the control beside them,
              which does something. */}
          <span className="shell__statuses">
            {realtime ? <RealtimeStatus state={realtime} /> : null}
            <StatusDot status={connection} />
          </span>
          <span className="shell__divider" aria-hidden="true" />
          <button
            type="button"
            className="button button--quiet button--small"
            onClick={onManageKey}
            // The label carries the state, because a key glyph alone cannot
            // distinguish "set" from "not set".
            aria-label={
              keyConfigured ? 'API access configured — manage it' : 'Configure API access'
            }
          >
            <KeyIcon />
            {keyConfigured ? 'API access' : 'Configure'}
          </button>
        </div>
      </header>

      {sidebar}

      {/* Only rendered as a real control below the breakpoint, where the
          sidebar overlays the content and needs a way out that is not the
          menu button. */}
      {sidebarOpen && onToggleSidebar ? (
        <button
          type="button"
          className="shell__scrim"
          aria-label="Close conversations"
          onClick={onToggleSidebar}
        />
      ) : null}

      {/* One landmark for the whole screen, so keyboard users can skip to it. */}
      <main className="shell__main" id="main">
        {children}
      </main>
    </div>
  );
}
