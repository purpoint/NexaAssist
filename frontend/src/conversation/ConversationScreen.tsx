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
import { useRealtime, type SocketFactory } from '../realtime/useRealtime';
import { Composer } from './Composer';
import { MessageList } from './MessageList';
import { useConversation } from './useConversation';

export function ConversationScreen({
  client,
  socketFactory,
  onAuthRequired,
  authenticated = false,
}: {
  client: ApiClient;
  /** Injected only by tests; production uses the global WebSocket. */
  socketFactory?: SocketFactory;
  /** Told when a request was refused for want of a credential. */
  onAuthRequired?: (required: boolean) => void;
  /** True when a key is configured, so the socket needs a ticket. */
  authenticated?: boolean;
}) {
  const conversation = useConversation(client);

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

  // Streaming is attempted, never assumed. `ask` returns false when the
  // socket is not open, and `send` falls back to HTTP in that case.
  const handleSend = (text: string) =>
    void conversation.send(text, {
      stream: realtime.ready ? realtime.ask : undefined,
    });

  const startConversation = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim()) return;
    await conversation.start(email.trim());
  };

  return (
    <section className="conversation" aria-label="Assistant">
      <header className="conversation__header">
        <div>
          <h1 className="conversation__title">Support assistant</h1>
          <p className="conversation__subtitle">
            {conversation.conversationId
              ? 'This exchange is being saved to your conversation.'
              : 'Ask a question, or start a conversation to keep the history.'}
          </p>
        </div>
        {conversation.conversationId ? (
          <button type="button" className="button button--quiet" onClick={conversation.reset}>
            New conversation
          </button>
        ) : null}
      </header>

      {conversation.conversationId ? null : (
        <form className="starter" onSubmit={startConversation}>
          <label className="starter__label" htmlFor="starter-email">
            Email
          </label>
          <input
            id="starter-email"
            className="starter__input"
            type="email"
            value={email}
            placeholder="you@example.com"
            onChange={(event) => setEmail(event.target.value)}
          />
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

      <Composer disabled={conversation.sending} onSend={handleSend} />
    </section>
  );
}
