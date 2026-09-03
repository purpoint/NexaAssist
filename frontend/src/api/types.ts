/**
 * The backend contract, in TypeScript.
 *
 * These mirror the schemas the API actually publishes (M17 and M18) rather
 * than a shape invented for the client's convenience. Where the two would
 * differ, the backend wins: a frontend type that disagrees with the server is
 * a bug that only shows up in production.
 *
 * Every field here corresponds to one in the OpenAPI document. Nothing is
 * optional unless the server says it is.
 */

/** The six categories the classifier may return. */
export type IntentCategory =
  | 'billing'
  | 'technical_support'
  | 'account'
  | 'product_question'
  | 'complaint'
  | 'other';

/** Why a handler was chosen, including why a fallback was used. */
export type RouteReason =
  | 'matched'
  | 'no_category'
  | 'low_confidence'
  | 'no_handler';

/** Why a request was sent to a person. */
export type EscalationReason =
  | 'unresolved'
  | 'low_confidence'
  | 'uncategorised'
  | 'policy'
  | 'complaint';

/** Provenance for one retrieved span, so a reader can check the claim. */
export interface Citation {
  document_id: string;
  document_title: string;
  /** Position of the chunk within its document. */
  ordinal: number;
  excerpt: string;
  /** Cosine similarity to the question: a retrieval score, not a confidence. */
  similarity: number;
}

export interface AssistantMessageRequest {
  message: string;
  /** Record this exchange against an existing conversation. */
  conversation_id?: string | null;
}

export interface AssistantMessageResponse {
  reply: string;
  intent: IntentCategory;
  /** Self-reported by the model, not calibrated. Useful for triage only. */
  confidence: number;
  handler: string;
  route_reason: RouteReason;
  fallback: boolean;
  /** False when the request was answered but not resolved. */
  handled: boolean;
  policy_modified: boolean;
  policy_rule: string | null;
  escalated: boolean;
  escalation_reasons: EscalationReason[];
  review_id: string | null;
  /** Empty whenever policy changed the reply -- provenance would be a lie. */
  citations: Citation[];
  conversation_id: string | null;
  /** Identifies the request, not its content. Safe to quote in a ticket. */
  trace_id: string | null;
}

export interface ConversationStartRequest {
  customer_email: string;
}

export interface Conversation {
  id: string;
  customer_id: string;
  created_at: string;
}

export type MessageRole = 'customer' | 'assistant';

export interface ConversationMessage {
  /** Explicit order, never inferred from the timestamp. */
  position: number;
  role: MessageRole;
  content: string;
  created_at: string;
}

export interface ConversationHistory {
  conversation_id: string;
  messages: ConversationMessage[];
}

/** The one error shape the API uses, across both transports. */
export interface ErrorResponse {
  code: string;
  message: string;
  details?: unknown;
}

export type ComponentStatus =
  | 'ok'
  | 'not_configured'
  | 'unavailable'
  | 'degraded';

export interface Readiness {
  status: string;
  database: ComponentStatus;
  components: Record<string, ComponentStatus>;
}
