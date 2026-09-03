/**
 * Whether the backend is answering, for the header indicator.
 *
 * Readiness rather than health: health only says the process is running, and
 * an indicator that stays green while the database is gone is worse than no
 * indicator at all.
 */

import { useCallback, useEffect, useState } from 'react';

import type { ApiClient } from '../api/client';
import type { ConnectionState } from '../components/Layout';

export function useReadiness(client: ApiClient, intervalMs = 30_000) {
  const [connection, setConnection] = useState<ConnectionState>('unknown');

  const check = useCallback(async () => {
    try {
      const readiness = await client.getReadiness();
      const degraded = Object.values(readiness.components).some(
        (status) => status === 'degraded' || status === 'unavailable',
      );
      setConnection(degraded ? 'degraded' : 'ok');
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

  return { connection, refresh: check };
}
