/**
 * The typed API client.
 *
 * One place that knows how to talk to the backend: the base URL, the error
 * shape, and the header a protected deployment expects. Components call
 * methods, never `fetch`, so a change to any of those is one edit.
 *
 * Every method returns parsed, typed data or throws `ApiError`. There is no
 * third outcome -- a caller that has to check both a return value and an
 * exception eventually forgets one.
 */

import { API_BASE_URL } from '../config';
import { ApiError } from './errors';
import type {
  AssistantMessageRequest,
  AssistantMessageResponse,
  Conversation,
  ConversationHistory,
  Readiness,
} from './types';

/** The header M19 expects when a deployment requires a key. */
export const API_KEY_HEADER = 'X-API-Key';

export interface ClientOptions {
  baseUrl?: string;
  /** Sent only when set, so an open deployment needs no configuration. */
  apiKey?: string | null;
  /** Injected so tests drive the client without a network or a global stub. */
  fetchImpl?: typeof fetch;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly apiKey: string | null;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? API_BASE_URL).replace(/\/+$/, '');
    this.apiKey = options.apiKey ?? null;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  // -- endpoints ----------------------------------------------------------

  /** Ask the assistant one question. */
  async sendMessage(
    request: AssistantMessageRequest,
  ): Promise<AssistantMessageResponse> {
    return this.request<AssistantMessageResponse>('POST', '/assistant/messages', request);
  }

  /** Open a conversation for a customer, creating them on first contact. */
  async startConversation(customerEmail: string): Promise<Conversation> {
    return this.request<Conversation>('POST', '/conversations', {
      customer_email: customerEmail,
    });
  }

  /** Confirm a stored conversation id is still valid before rendering. */
  async getConversation(conversationId: string): Promise<Conversation> {
    return this.request<Conversation>('GET', `/conversations/${conversationId}`);
  }

  /** Turns in reading order; `limit` returns the most recent, still oldest-first. */
  async getHistory(
    conversationId: string,
    limit?: number,
  ): Promise<ConversationHistory> {
    const query = limit === undefined ? '' : `?limit=${limit}`;
    return this.request<ConversationHistory>(
      'GET',
      `/conversations/${conversationId}/messages${query}`,
    );
  }

  /** Component-level status, for a connection indicator. */
  async getReadiness(): Promise<Readiness> {
    return this.request<Readiness>('GET', '/ready');
  }

  // -- transport ----------------------------------------------------------

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    if (this.apiKey) {
      headers[API_KEY_HEADER] = this.apiKey;
    }

    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      // A DNS failure, a refused connection, an offline browser. The cause is
      // not useful to a user and not safe to render.
      throw ApiError.unreachable();
    }

    const payload = await readJson(response);
    if (!response.ok) {
      throw ApiError.fromResponse(response.status, payload);
    }
    return payload as T;
  }
}

async function readJson(response: Response): Promise<unknown> {
  // A 204, an empty error body, or an HTML proxy page must not become a
  // parse exception that hides the status that actually mattered.
  try {
    const text = await response.text();
    return text ? (JSON.parse(text) as unknown) : null;
  } catch {
    return null;
  }
}
