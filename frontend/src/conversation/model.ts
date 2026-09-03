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
  /**
   * True for a reply that arrived over the socket.
   *
   * Streamed replies come from the realtime path, which answers in prose and
   * produces no citations -- the grounded pipeline behind the HTTP endpoint is
   * the one that rebuilds them from retrieval. The flag exists so the UI can
   * say that rather than let an absence of sources read as "no sources were
   * needed".
   */
  streamed?: boolean;
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
