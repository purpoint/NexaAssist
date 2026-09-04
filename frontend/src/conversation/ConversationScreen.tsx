/**
 * The assistant screen.
 *
 * A conversation is optional by design: the backend answers a message without
 * one, so somebody can ask a question before deciding to identify themselves.
 * Starting one is an explicit act, and resuming happens on its own from a
 * stored id.
 */

import { useEffect, useState, type FormEvent } from 'react';

import type { ApiClient } from '../api/client';
import { EmptyState, ErrorBanner, Spinner } from '../components/primitives';
import { socketUrlWithTicket, WS_URL } from '../config';
import { useRealtime, type RealtimeState, type SocketFactory } from '../realtime/useRealtime';
import { Composer } from './Composer';
import { MessageList } from './MessageList';
import type { useConversation } from './useConversation';
import { UNTITLED, type useConversationIndex } from './useConversationIndex';

export function ConversationScreen({
  client,
  conversation,
  index,
  socketFactory,
  onAuthRequired,
  onRealtimeState,
  authenticated = false,
}: {
  client: ApiClient;
  /** Owned by the root, because the sidebar switches between conversations. */
  conversation: ReturnType<typeof useConversation>;
  index: ReturnType<typeof useConversationIndex>;
  /** Injected only by tests; production uses the global WebSocket. */
  socketFactory?: SocketFactory;
  /** Told when a request was refused for want of a credential. */
  onAuthRequired?: (required: boolean) => void;
  /** Reports the socket's state so the shell can show it. */
  onRealtimeState?: (state: RealtimeState) => void;
  /** True when a key is configured, so the socket needs a ticket. */
  authenticated?: boolean;
}) {
  useEffect(() => {
    onAuthRequired?.(conversation.authRequired);
  }, [conversation.authRequired, onAuthRequired]);
  const [email, setEmail] = useState('');

  const realtime = useRealtime(
    WS_URL,
    {
      onDelta: conversation.appendDelta,
      onComplete: conversation.completeStream,
      onError: (_code, message) => conversation.failStream(message),
    },
    {
      socketFactory,
      // Only when a key is configured. An open deployment needs no ticket,
      // and asking for one would fail and disable streaming for no reason.
      getTicket: authenticated
        ? async () => (await client.mintRealtimeTicket()).ticket
        : undefined,
      urlWithTicket: (_base, ticket) => socketUrlWithTicket(ticket),
    },
  );

  useEffect(() => {
    onRealtimeState?.(realtime.state);
  }, [realtime.state, onRealtimeState]);

  // Streaming is attempted, never assumed. `ask` returns false when the
  // socket is not open, and `send` falls back to HTTP in that case.
  const handleSend = (text: string) => {
    // Name the conversation from its first question, so the sidebar shows
    // something a reader can recognise.
    if (conversation.conversationId) {
      index.nameIfUnnamed(conversation.conversationId, text);
    }
    void conversation.send(text, {
      stream: realtime.ready ? realtime.ask : undefined,
    });
  };

  const startConversation = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim()) return;
    const id = await conversation.start(email.trim());
    // Indexed only once the server has actually opened it: an entry for a
    // conversation that failed to start would point at nothing.
    if (id) index.remember(id, UNTITLED);
  };

  return (
    <section className="conversation" aria-label="Assistant">
      <header className="conversation__head">
        <div>
          <h1 className="conversation__title">Support assistant</h1>
          <p className="conversation__subtitle">
            {conversation.conversationId
              ? 'This exchange is being saved to your conversation.'
              : 'Ask a question, or start a conversation to keep the history.'}
          </p>
        </div>
      </header>

      {/* The body scrolls; the composer below it does not, so it never
          scrolls away from somebody mid-conversation. */}
      <div className="conversation__body">
        <div className="conversation__inner">
          {conversation.conversationId ? null : (
            <form className="starter" onSubmit={startConversation}>
              <div className="starter__field">
                <label className="starter__label" htmlFor="starter-email">
                  Email
                </label>
                <input
                  id="starter-email"
                  className="field"
                  type="email"
                  value={email}
                  placeholder="you@example.com"
                  onChange={(event) => setEmail(event.target.value)}
                />
              </div>
              <button
                type="submit"
                className="button"
                disabled={conversation.loading || email.trim().length === 0}
              >
                Start conversation
              </button>
            </form>
          )}

          {/* The key panel already explains a 401 and offers the fix; a banner
              beside it would say the same thing twice. */}
          {conversation.error && !conversation.authRequired ? (
            <ErrorBanner message={conversation.error} />
          ) : null}

          {conversation.loading && conversation.turns.length === 0 ? (
            <div className="conversation__loading">
              <Spinner label="Loading your conversation" />
            </div>
          ) : null}

          {conversation.turns.length === 0 && !conversation.loading ? (
            <EmptyState title="No messages yet">
              Ask anything about your account, a charge, or how something works.
            </EmptyState>
          ) : (
            <MessageList turns={conversation.turns} sending={conversation.sending} />
          )}
        </div>
      </div>

      <Composer disabled={conversation.sending} onSend={handleSend} />
    </section>
  );
}
