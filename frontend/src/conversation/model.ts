/**
 * What the UI shows for one exchange.
 *
 * Deliberately not the server's `ConversationMessage`. A rendered turn has
 * things the server has no opinion about -- whether it is still being sent,
 * whether it failed, the citations that arrived with it -- and forcing the
 * wire type to carry them would put view state into the contract.
 */

import type { Citation } from '../api/types';

export type TurnStatus = 'sent' | 'pending' | 'failed';

export interface Turn {
  /** Stable across a re-render, and across the reply arriving. */
  id: string;
  role: 'customer' | 'assistant';
  text: string;
  status: TurnStatus;
  citations: Citation[];
  /** Quotable in a support ticket; identifies the request, not its content. */
  traceId?: string | null;
  escalated?: boolean;
  /** True for a reply that arrived over the socket. */
  streamed?: boolean;
  /**
   * True when the answer came from the assistant pipeline -- classified,
   * retrieved against the knowledge base, checked by policy.
   *
   * The socket used to answer in prose and produce no citations, so `streamed`
   * alone was enough to warn a reader that an absence of sources did not mean
   * none were needed. It now runs the same pipeline as the HTTP path, so the
   * warning belongs to answers that really are unsourced -- which is the
   * fallback the server uses when it has no database -- and not to every
   * answer that happened to arrive over a socket.
   */
  grounded?: boolean;
  /** True while deltas are still arriving. */
  streaming?: boolean;
}

let counter = 0;

/** Ids are local to this session; the server's ordering is `position`. */
export function nextTurnId(prefix: string): string {
  counter += 1;
  return `${prefix}-${counter}`;
}

export function resetTurnIds(): void {
  counter = 0;
}
