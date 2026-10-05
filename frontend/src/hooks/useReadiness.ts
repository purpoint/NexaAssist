/**
 * Whether the backend is answering, for the header indicator.
 *
 * Readiness rather than health: health only says the process is running, and
 * an indicator that stays green while the database is gone is worse than no
 * indicator at all.
 *
 * It also answers a second question the client would otherwise have to learn
 * the hard way: whether this deployment requires a credential. Without that,
 * a visitor with no key opens a WebSocket the server will refuse, and only
 * finds out when the connection dies -- which on a deployed instance took
 * ninety seconds to surface as "something went wrong".
 */

import { useCallback, useEffect, useState } from 'react';

import type { ApiClient } from '../api/client';
import type { ConnectionState } from '../components/Layout';

export function useReadiness(client: ApiClient, intervalMs = 30_000) {
  const [connection, setConnection] = useState<ConnectionState>('unknown');
  // null until the first answer arrives. Not false: "we have not asked yet"
  // and "this deployment is open" lead to different decisions, and defaulting
  // to the second would have the client open a socket it may not be allowed.
  const [authEnforced, setAuthEnforced] = useState<boolean | null>(null);

  const check = useCallback(async () => {
    try {
      const readiness = await client.getReadiness();
      const degraded = Object.values(readiness.components).some(
        (status) => status === 'degraded' || status === 'unavailable',
      );
      setConnection(degraded ? 'degraded' : 'ok');
      // Whether this deployment demands a credential. The server reports it
      // plainly, so the client does not have to discover it by being refused.
      setAuthEnforced(readiness.components.authentication !== 'not_configured');
    } catch {
      // The indicator's whole job is to survive the failure it reports.
      setConnection('down');
    }
  }, [client]);

  useEffect(() => {
    let cancelled = false;
    const run = () => {
      if (!cancelled) void check();
    };
    run();
    const timer = window.setInterval(run, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [check, intervalMs]);

  return { connection, authEnforced, refresh: check };
}
