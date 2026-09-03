/**
 * Application root.
 *
 * The shell and the client live here so every screen shares one client
 * instance -- a client per screen would mean a key configured in one place and
 * missing in another.
 */

import { useMemo } from 'react';

import { ApiClient } from './api/client';
import { Layout } from './components/Layout';
import { EmptyState } from './components/primitives';
import { useReadiness } from './hooks/useReadiness';

export default function App() {
  const client = useMemo(() => new ApiClient(), []);
  const { connection } = useReadiness(client);

  return (
    <Layout connection={connection}>
      <EmptyState title="Nothing here yet">
        The assistant arrives in the next commit. The shell, the typed API
        client, and the connection indicator are wired to the real backend.
      </EmptyState>
    </Layout>
  );
}
