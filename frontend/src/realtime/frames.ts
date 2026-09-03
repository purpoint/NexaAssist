/**
 * The WebSocket wire contract, transcribed from M14/M18.
 *
 * FastAPI does not describe WebSocket routes in OpenAPI, so the server's
 * vocabulary is pinned by its tests and this is the client's copy of it. The
 * frame names are exactly the server's -- ping/ask outbound, and
 * ready/pong/delta/complete/error inbound. Nothing is invented here: a frame
 * type the server does not send is a frame that will never arrive.
 */

export type ClientFrame =
  | { type: 'ping' }
  | { type: 'ask'; question: string; conversation_id?: string | null };

export interface ReadyFrame {
  type: 'ready';
  connection_id: string;
  protocol_version: number;
}

export interface PongFrame {
  type: 'pong';
}

export interface DeltaFrame {
  type: 'delta';
  text: string;
}

export interface CompleteFrame {
  type: 'complete';
  text: string;
  deltas: number;
  conversation_id: string | null;
}

export interface ErrorFrame {
  type: 'error';
  code: string;
  message: string;
  details?: unknown;
}

export type ServerFrame =
  | ReadyFrame
  | PongFrame
  | DeltaFrame
  | CompleteFrame
  | ErrorFrame;

/**
 * Parse an inbound frame, returning null for anything unrecognised.
 *
 * A frame the client does not understand is ignored rather than thrown on: a
 * server that adds a type should not break a client that predates it, and an
 * exception in a socket handler tears down the connection.
 */
export function parseServerFrame(raw: string): ServerFrame | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) return null;

  const frame = parsed as { type?: unknown };
  switch (frame.type) {
    case 'ready':
    case 'pong':
    case 'delta':
    case 'complete':
    case 'error':
      return parsed as ServerFrame;
    default:
      return null;
  }
}
