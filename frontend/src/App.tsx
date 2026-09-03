/**
 * Application root.
 *
 * The shell and the client live here so every screen shares one client
 * instance -- a client per screen would mean a key configured in one place
 * and missing in another.
 */

import { useMemo } from 'react';

import { ApiClient } from './api/client';
import { Layout } from './components/Layout';
import { ConversationScreen } from './conversation/ConversationScreen';
import { useReadiness } from './hooks/useReadiness';

export default function App() {
  const client = useMemo(() => new ApiClient(), []);
  const { connection } = useReadiness(client);

  return (
    <Layout connection={connection}>
      <ConversationScreen client={client} />
    </Layout>
  );
}
