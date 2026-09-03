/**
 * The realtime connection.
 *
 * Owns the socket's whole life: connecting, reconnecting with backoff, and
 * giving up. A component should be able to ask "can I stream right now" and
 * get a truthful answer without knowing any of that.
 *
 * Reconnection backs off and *stops*. A client that retries forever against a
 * server that is not coming back is a client that hammers an outage, and a
 * connection indicator that never settles tells the user nothing. After the
 * cap the state is `unavailable`, which is what makes the HTTP fallback a
 * decision rather than an accident.
 *
 * One question is in flight at a time, matching the server's own single-flight
 * rule. Deltas are matched to the request that is open; anything arriving
 * after a complete is discarded, so a late frame from a previous attempt can
 * never append itself to the next answer.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { parseServerFrame, type ClientFrame } from './frames';

export type RealtimeState = 'connecting' | 'open' | 'reconnecting' | 'unavailable';

const MAX_ATTEMPTS = 5;
const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 8_000;

export interface RealtimeHandlers {
  onDelta: (text: string) => void;
  onComplete: (text: string, conversationId: string | null) => void;
  onError: (code: string, message: string) => void;
}

/** Injected so tests drive the socket without a server or a global stub. */
export type SocketFactory = (url: string) => WebSocket;

export function useRealtime(
  url: string,
  handlers: RealtimeHandlers,
  options: { enabled?: boolean; socketFactory?: SocketFactory } = {},
) {
  const { enabled = true, socketFactory } = options;

  const [state, setState] = useState<RealtimeState>('connecting');
  const socketRef = useRef<WebSocket | null>(null);
  const attemptsRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const closedByUs = useRef(false);
  // True between sending an ask and its terminating frame.
  const awaitingRef = useRef(false);

  // Handlers and the factory change identity on every render when a caller
  // passes them inline. Holding both in refs keeps `connect` stable -- with
  // either in the dependency list, every render tore down the socket and
  // opened a new one, which looks like a server that will not stay up.
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  const factoryRef = useRef(socketFactory);
  factoryRef.current = socketFactory;

  const connect = useCallback(() => {
    if (!enabled) return;
    const create =
      factoryRef.current ?? ((target: string) => new WebSocket(target));

    let socket: WebSocket;
    try {
      socket = create(url);
    } catch {
      setState('unavailable');
      return;
    }
    socketRef.current = socket;

    socket.onopen = () => {
      attemptsRef.current = 0;
      setState('open');
    };

    socket.onmessage = (event: MessageEvent) => {
      const frame = parseServerFrame(String(event.data));
      if (!frame) return;

      switch (frame.type) {
        case 'delta':
          if (awaitingRef.current) handlersRef.current.onDelta(frame.text);
          break;
        case 'complete':
          if (awaitingRef.current) {
            awaitingRef.current = false;
            handlersRef.current.onComplete(frame.text, frame.conversation_id);
          }
          break;
        case 'error':
          // Reported whether or not a question is open: a refusal to record
          // arrives before any delta.
          awaitingRef.current = false;
          handlersRef.current.onError(frame.code, frame.message);
          break;
        default:
          // ready and pong need no handling beyond the state the open event
          // already set.
          break;
      }
    };

    socket.onclose = () => {
      socketRef.current = null;
      awaitingRef.current = false;
      if (closedByUs.current) return;

      attemptsRef.current += 1;
      if (attemptsRef.current > MAX_ATTEMPTS) {
        setState('unavailable');
        return;
      }
      setState('reconnecting');
      const delay = Math.min(
        BASE_DELAY_MS * 2 ** (attemptsRef.current - 1),
        MAX_DELAY_MS,
      );
      timerRef.current = window.setTimeout(connect, delay);
    };

    socket.onerror = () => {
      // Close handling owns reconnection; an error without a close would
      // otherwise schedule a second attempt for the same failure.
    };
  }, [enabled, url]);

  useEffect(() => {
    if (!enabled) {
      setState('unavailable');
      return;
    }
    closedByUs.current = false;
    connect();
    return () => {
      closedByUs.current = true;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [connect, enabled]);

  const send = useCallback((frame: ClientFrame): boolean => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(frame));
    return true;
  }, []);

  /** Ask a question. Returns false when the caller must fall back to HTTP. */
  const ask = useCallback(
    (question: string, conversationId: string | null): boolean => {
      if (awaitingRef.current) return false;
      const sent = send({
        type: 'ask',
        question,
        conversation_id: conversationId,
      });
      if (sent) awaitingRef.current = true;
      return sent;
    },
    [send],
  );

  return {
    state,
    /** True only when a question can actually be streamed right now. */
    ready: state === 'open',
    ask,
  };
}
