/**
 * The conversation's state machine.
 *
 * Kept out of the components because this is where the interesting decisions
 * live -- when a turn is optimistic, what happens when sending fails, how a
 * stored id is resumed -- and a decision buried in JSX is a decision nobody
 * reviews.
 *
 * The customer's turn is shown immediately, before the server has seen it.
 * Waiting for the round trip to render what somebody just typed makes the
 * product feel broken on a slow connection; the turn carries a `pending`
 * status so the optimism is visible rather than a lie.
 */

import type { StreamResult } from '../realtime/useRealtime';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { ApiClient } from '../api/client';
import { ApiError } from '../api/errors';
import type { ConversationMessage } from '../api/types';
import { nextTurnId, type Turn } from './model';

const STORAGE_KEY = 'nexaassist.conversation_id';

/** Reading storage can throw in a private window; a missing id is not fatal. */
function readStoredId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredId(id: string | null): void {
  try {
    if (id === null) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // A conversation that cannot be remembered still works for this session.
  }
}

function toTurn(message: ConversationMessage): Turn {
  return {
    id: `server-${message.position}`,
    role: message.role,
    text: message.content,
    status: 'sent',
    citations: [],
  };
}

function isUnauthenticated(caught: unknown): boolean {
  return caught instanceof ApiError && caught.unauthenticated;
}

function messageFor(caught: unknown): string {
  if (caught instanceof ApiError) return caught.message;
  // Never render an unknown throwable: it may carry a stack.
  return 'Something went wrong. Please try again.';
}

/**
 * What kind of failure this was, for the UI to present.
 *
 * A category rather than the exception, so a component decides what to show
 * without reaching into an error class -- and so nothing that carries a stack
 * ever reaches a render.
 */
export type FailureKind =
  | 'offline'
  | 'unauthenticated'
  | 'rate_limited'
  | 'server'
  | 'request';

export interface Failure {
  kind: FailureKind;
  /** The server's own message, which the contract guarantees is safe. */
  message: string;
  /** True when running the same request again could plausibly work. */
  retryable: boolean;
}

function failureFrom(caught: unknown): Failure {
  if (!(caught instanceof ApiError)) {
    return { kind: 'server', message: messageFor(caught), retryable: true };
  }
  const kind: FailureKind =
    caught.status === 0
      ? 'offline'
      : caught.unauthenticated
        ? 'unauthenticated'
        : caught.rateLimited
          ? 'rate_limited'
          : caught.status >= 500
            ? 'server'
            : 'request';
  return { kind, message: caught.message, retryable: caught.retryable };
}

export interface SendOptions {
  /**
   * Stream over the socket when it can. Returns false when it could not, and
   * the caller falls back to HTTP -- which is the only way a refusal to
   * record, or a socket that never opened, does not lose the question.
   */
  stream?: (question: string, conversationId: string | null) => boolean;
}

export function useConversation(client: ApiClient) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  // Distinct from `error`: a 401 is fixable by the user, and the fix is a
  // specific one. Collapsing it into the error banner would tell somebody
  // that something went wrong without telling them what to do about it.
  const [authRequired, setAuthRequired] = useState(false);

  // Held in a ref so a re-render never re-triggers resumption.
  const resumed = useRef(false);

  const loadHistory = useCallback(
    async (id: string) => {
      setLoading(true);
      try {
        const history = await client.getHistory(id);
        setTurns(history.messages.map(toTurn));
        setConversationId(id);
        setFailure(null);
        setAuthRequired(false);
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 404) {
          // The stored id is stale -- a cleared database, or a different
          // deployment. Forget it rather than showing a permanent error for
          // something the user cannot act on.
          writeStoredId(null);
          setConversationId(null);
          setTurns([]);
        } else {
          setAuthRequired(isUnauthenticated(caught));
          setFailure(failureFrom(caught));
        }
      } finally {
        setLoading(false);
      }
    },
    [client],
  );

  useEffect(() => {
    if (resumed.current) return;
    resumed.current = true;
    const stored = readStoredId();
    if (stored) void loadHistory(stored);
  }, [loadHistory]);

  const start = useCallback(
    async (customerEmail: string) => {
      setLoading(true);
      try {
        const conversation = await client.startConversation(customerEmail);
        writeStoredId(conversation.id);
        setConversationId(conversation.id);
        setTurns([]);
        setFailure(null);
        setAuthRequired(false);
        return conversation.id;
      } catch (caught) {
        setAuthRequired(isUnauthenticated(caught));
        setFailure(failureFrom(caught));
        return null;
      } finally {
        setLoading(false);
      }
    },
    [client],
  );

  /** The assistant turn currently being streamed into, if any. */
  const streamingId = useRef<string | null>(null);

  const appendDelta = useCallback((text: string) => {
    const target = streamingId.current;
    if (target === null) return; // A late frame from an abandoned attempt.
    setTurns((current) =>
      current.map((turn) =>
        turn.id === target ? { ...turn, text: turn.text + text } : turn,
      ),
    );
  }, []);

  const completeStream = useCallback(
    (text: string, streamConversationId: string | null, result: StreamResult) => {
      const target = streamingId.current;
      streamingId.current = null;
      setSending(false);
      if (target === null) return;
      // The complete frame carries the whole answer, so it replaces the
      // accumulation rather than being appended to it -- otherwise a
      // duplicated delta would show twice. It also carries what the answer
      // turned out to be: sourced or not, escalated or not.
      setTurns((current) =>
        current.map((turn) =>
          turn.id === target
            ? {
                ...turn,
                text,
                status: 'sent' as const,
                streaming: false,
                citations: result.citations,
                escalated: result.escalated,
                grounded: result.grounded,
              }
            : turn.status === 'pending'
              ? { ...turn, status: 'sent' as const }
              : turn,
        ),
      );
      if (streamConversationId && streamConversationId !== conversationId) {
        setConversationId(streamConversationId);
        writeStoredId(streamConversationId);
      }
    },
    [conversationId],
  );

  const failStream = useCallback((message: string) => {
    const target = streamingId.current;
    streamingId.current = null;
    setSending(false);
    // A socket error frame carries the server's code and message but no HTTP
    // status; treat it as a server-side failure worth retrying, which is what
    // every code the socket can send actually is.
    setFailure({ kind: 'server', message, retryable: true });
    setTurns((current) =>
      current
        // Drop the empty placeholder: an assistant bubble with no text is
        // worse than no bubble.
        .filter((turn) => !(turn.id === target && turn.text === ''))
        .map((turn) =>
          turn.status === 'pending' ? { ...turn, status: 'failed' as const } : turn,
        ),
    );
  }, []);

  const send = useCallback(
    async (text: string, options: SendOptions = {}) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;

      const pendingId = nextTurnId('local');
      setTurns((current) => [
        ...current,
        {
          id: pendingId,
          role: 'customer',
          text: trimmed,
          status: 'pending',
          citations: [],
        },
      ]);
      setSending(true);
      setFailure(null);
      setAuthRequired(false);

      // Try the socket first. It returns false when it could not send, and
      // the HTTP path below runs instead -- so a closed socket, or a server
      // that refuses to record, never loses the question.
      if (options.stream?.(trimmed, conversationId)) {
        const replyId = nextTurnId('stream');
        streamingId.current = replyId;
        setTurns((current) => [
          ...current,
          {
            id: replyId,
            role: 'assistant',
            text: '',
            status: 'pending',
            citations: [],
            streamed: true,
            streaming: true,
          },
        ]);
        return;
      }

      try {
        const reply = await client.sendMessage({
          message: trimmed,
          conversation_id: conversationId,
        });
        setTurns((current) => [
          ...current.map((turn) =>
            turn.id === pendingId ? { ...turn, status: 'sent' as const } : turn,
          ),
          {
            id: nextTurnId('reply'),
            role: 'assistant',
            text: reply.reply,
            status: 'sent',
            citations: reply.citations ?? [],
            traceId: reply.trace_id,
            escalated: reply.escalated,
          },
        ]);
        if (reply.conversation_id && reply.conversation_id !== conversationId) {
          setConversationId(reply.conversation_id);
          writeStoredId(reply.conversation_id);
        }
      } catch (caught) {
        // The question stays on screen, marked failed. Removing it would
        // discard what somebody typed because the network hiccuped.
        setTurns((current) =>
          current.map((turn) =>
            turn.id === pendingId ? { ...turn, status: 'failed' as const } : turn,
          ),
        );
        setAuthRequired(isUnauthenticated(caught));
        setFailure(failureFrom(caught));
      } finally {
        setSending(false);
      }
    },
    [client, conversationId, sending],
  );

  const reset = useCallback(() => {
    streamingId.current = null;
    writeStoredId(null);
    setConversationId(null);
    setTurns([]);
    setFailure(null);
    setAuthRequired(false);
  }, []);

  return {
    conversationId,
    turns,
    sending,
    loading,
    failure,
    /** The message alone, for callers that only render text. */
    error: failure?.message ?? null,
    authRequired,
    start,
    send,
    reset,
    loadHistory,
    appendDelta,
    completeStream,
    failStream,
  };
}

export const CONVERSATION_STORAGE_KEY = STORAGE_KEY;
