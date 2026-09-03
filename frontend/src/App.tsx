/**
 * Application root.
 *
 * The client is rebuilt whenever the key changes, so a key saved mid-session
 * takes effect on the next request without a reload. Everything below shares
 * that one instance -- a client per screen would mean a key configured in one
 * place and missing in another.
 */

import { useCallback, useMemo, useState } from 'react';

import { ApiClient } from './api/client';
import { ApiKeyPanel } from './auth/ApiKeyPanel';
import { useApiKey } from './auth/useApiKey';
import { Layout } from './components/Layout';
import { ConversationScreen } from './conversation/ConversationScreen';
import { useReadiness } from './hooks/useReadiness';

export default function App() {
  const { apiKey, save, clear, configured } = useApiKey();
  const client = useMemo(() => new ApiClient({ apiKey }), [apiKey]);
  const { connection, refresh } = useReadiness(client);

  const [panelOpen, setPanelOpen] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);

  const handleSave = useCallback(
    (key: string) => {
      save(key);
      setAuthRequired(false);
      setPanelOpen(false);
      // The indicator was measured without the key; measure it again with one.
      void refresh();
    },
    [refresh, save],
  );

  // A refused request opens the panel itself: being told "authentication is
  // required" and then having to hunt for where to type the key is a bad way
  // to learn the deployment is protected.
  const showPanel = panelOpen || authRequired;

  return (
    <Layout
      connection={connection}
      keyConfigured={configured}
      onManageKey={() => setPanelOpen((open) => !open)}
    >
      {showPanel ? (
        <ApiKeyPanel
          configured={configured}
          required={authRequired}
          onSave={handleSave}
          onClear={() => {
            clear();
            void refresh();
          }}
          onDismiss={() => setPanelOpen(false)}
        />
      ) : null}
      <ConversationScreen client={client} onAuthRequired={setAuthRequired} />
    </Layout>
  );
}
