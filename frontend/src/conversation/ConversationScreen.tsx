/**
 * The assistant screen.
 *
 * A conversation is optional by design: the backend answers a message without
 * one, so somebody can ask a question before deciding to identify themselves.
 * Starting one is an explicit act, and resuming happens on its own from a
 * stored id.
 */

import { useState, type FormEvent } from 'react';

import type { ApiClient } from '../api/client';
import { EmptyState, ErrorBanner, Spinner } from '../components/primitives';
import { Composer } from './Composer';
import { MessageList } from './MessageList';
import { useConversation } from './useConversation';

export function ConversationScreen({ client }: { client: ApiClient }) {
  const conversation = useConversation(client);
  const [email, setEmail] = useState('');

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

      {conversation.error ? <ErrorBanner message={conversation.error} /> : null}

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

      <Composer disabled={conversation.sending} onSend={conversation.send} />
    </section>
  );
}
