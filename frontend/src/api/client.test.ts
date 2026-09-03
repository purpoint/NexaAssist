/** The client's contract: typed results, one error type, no surprises. */

import { describe, expect, it, vi } from 'vitest';

import { ApiClient, API_KEY_HEADER } from './client';
import { ApiError, UNREACHABLE_CODE } from './errors';

const BASE = 'http://api.test/api/v1';

function respondWith(status: number, body: unknown, ok = status < 400): Response {
  return {
    ok,
    status,
    text: async () => (body === null ? '' : JSON.stringify(body)),
  } as unknown as Response;
}

/** Run a call that must fail, and hand back the typed error. */
async function failureOf(call: Promise<unknown>): Promise<ApiError> {
  try {
    await call;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error('expected the call to fail');
}

function clientReturning(response: Response, apiKey?: string) {
  const fetchImpl = vi.fn().mockResolvedValue(response);
  return {
    client: new ApiClient({ baseUrl: BASE, apiKey, fetchImpl: fetchImpl as never }),
    fetchImpl,
  };
}

describe('requests', () => {
  it('posts a message and returns the parsed reply', async () => {
    const { client, fetchImpl } = clientReturning(
      respondWith(200, { reply: 'hello', citations: [], conversation_id: null }),
    );

    const reply = await client.sendMessage({ message: 'hi' });

    expect(reply.reply).toBe('hello');
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`${BASE}/assistant/messages`);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ message: 'hi' });
  });

  it('sends the key header only when one is configured', async () => {
    const open = clientReturning(respondWith(200, {}));
    await open.client.getReadiness();
    expect(open.fetchImpl.mock.calls[0][1].headers[API_KEY_HEADER]).toBeUndefined();

    const keyed = clientReturning(respondWith(200, {}), 'secret-key');
    await keyed.client.getReadiness();
    expect(keyed.fetchImpl.mock.calls[0][1].headers[API_KEY_HEADER]).toBe('secret-key');
  });

  it('never puts the key in the url, where proxies and history would see it', async () => {
    const { client, fetchImpl } = clientReturning(respondWith(200, {}), 'secret-key');
    await client.getHistory('abc', 5);
    expect(String(fetchImpl.mock.calls[0][0])).not.toContain('secret-key');
  });

  it('builds conversation paths from the id', async () => {
    const { client, fetchImpl } = clientReturning(respondWith(200, { messages: [] }));
    await client.getHistory('abc', 5);
    expect(fetchImpl.mock.calls[0][0]).toBe(`${BASE}/conversations/abc/messages?limit=5`);
  });

  it('omits the limit when none is given', async () => {
    const { client, fetchImpl } = clientReturning(respondWith(200, { messages: [] }));
    await client.getHistory('abc');
    expect(fetchImpl.mock.calls[0][0]).toBe(`${BASE}/conversations/abc/messages`);
  });

  it('never doubles a slash when the base url has a trailing one', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(respondWith(200, {}));
    const client = new ApiClient({ baseUrl: `${BASE}/`, fetchImpl: fetchImpl as never });
    await client.getReadiness();
    expect(fetchImpl.mock.calls[0][0]).toBe(`${BASE}/ready`);
  });
});

describe('failures', () => {
  it('turns the server error shape into an ApiError', async () => {
    const { client } = clientReturning(
      respondWith(404, { code: 'conversation_not_found', message: 'Not found.' }),
    );

    await expect(client.getConversation('missing')).rejects.toMatchObject({
      code: 'conversation_not_found',
      status: 404,
    });
  });

  it('does not surface an unreadable body', async () => {
    // A proxy returning HTML must not become raw markup on screen.
    const { client } = clientReturning(respondWith(502, null, false));
    const error = await failureOf(client.getReadiness());
    expect(error.code).toBe('unexpected_error');
    expect(error.message).not.toContain('<');
  });

  it('reports an unreachable server rather than throwing a fetch error', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    const client = new ApiClient({ baseUrl: BASE, fetchImpl: fetchImpl as never });

    const error = await failureOf(client.getReadiness());
    expect(error.code).toBe(UNREACHABLE_CODE);
    expect(error.status).toBe(0);
    expect(error.retryable).toBe(true);
  });

  it('classifies the statuses a caller must act on differently', () => {
    expect(new ApiError('x', 'm', 401).unauthenticated).toBe(true);
    expect(new ApiError('x', 'm', 429).rateLimited).toBe(true);
    expect(new ApiError('x', 'm', 429).retryable).toBe(true);
    expect(new ApiError('x', 'm', 503).retryable).toBe(true);
    // A client mistake will not fix itself on a retry.
    expect(new ApiError('x', 'm', 422).retryable).toBe(false);
    expect(new ApiError('x', 'm', 404).retryable).toBe(false);
  });
});
