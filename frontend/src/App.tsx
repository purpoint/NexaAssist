/**
 * Application root, and the composition point.
 *
 * The conversation's state lives here rather than in the screen because two
 * regions need it: the screen renders it, and the sidebar switches between
 * conversations. State owned by one of them and reached into by the other is
 * the arrangement that eventually disagrees with itself.
 *
 * The realtime connection stays in the screen, which is the only thing that
 * streams; the header is told its state through a setter so the indicator can
 * live in the shell without the socket doing so.
 *
 * The client is rebuilt whenever the key changes, so a key saved mid-session
 * takes effect on the next request without a reload.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { ApiClient } from './api/client';
import { ApiKeyPanel } from './auth/ApiKeyPanel';
import { useApiKey } from './auth/useApiKey';
import { Layout } from './components/Layout';
import { Sidebar } from './components/Sidebar';
import { ConversationScreen } from './conversation/ConversationScreen';
import { useConversation } from './conversation/useConversation';
import { UNTITLED, useConversationIndex } from './conversation/useConversationIndex';
import { useReadiness } from './hooks/useReadiness';
import type { RealtimeState } from './realtime/useRealtime';

export default function App() {
  const { apiKey, save, clear, configured } = useApiKey();
  const client = useMemo(() => new ApiClient({ apiKey }), [apiKey]);
  const { connection, refresh } = useReadiness(client);

  const conversation = useConversation(client);
  const index = useConversationIndex();

  const [realtime, setRealtime] = useState<RealtimeState>('connecting');
  const [panelOpen, setPanelOpen] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // A conversation resumed from storage on mount is one this browser has seen
  // before; make sure it is in the list rather than silently missing from it.
  const { remember } = index;
  const { conversationId } = conversation;
  useEffect(() => {
    if (conversationId) remember(conversationId, UNTITLED);
  }, [conversationId, remember]);

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

  const openConversation = useCallback(
    (id: string) => {
      setSidebarOpen(false);
      void conversation.loadHistory(id);
    },
    [conversation],
  );

  const startFresh = useCallback(() => {
    setSidebarOpen(false);
    conversation.reset();
  }, [conversation]);

  // A refused request opens the panel itself: being told "authentication is
  // required" and then having to hunt for where to type the key is a bad way
  // to learn the deployment is protected.
  const showPanel = panelOpen || authRequired;

  return (
    <Layout
      connection={connection}
      realtime={realtime}
      keyConfigured={configured}
      onManageKey={() => setPanelOpen((open) => !open)}
      sidebarOpen={sidebarOpen}
      onToggleSidebar={() => setSidebarOpen((open) => !open)}
      sidebar={
        <Sidebar
          entries={index.entries}
          activeId={conversation.conversationId}
          open={sidebarOpen}
          onSelect={openConversation}
          onNew={startFresh}
        />
      }
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
      <ConversationScreen
        client={client}
        conversation={conversation}
        index={index}
        onAuthRequired={setAuthRequired}
        onRealtimeState={setRealtime}
        authenticated={configured}
      />
    </Layout>
  );
}
