/** The conversation state machine: optimism, failure, and resumption. */

import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiClient } from '../api/client';
import { ApiError } from '../api/errors';
import type { AssistantMessageResponse } from '../api/types';
import { resetTurnIds } from './model';
import { CONVERSATION_STORAGE_KEY, useConversation } from './useConversation';

const CONVERSATION_ID = '11111111-1111-1111-1111-111111111111';

function reply(overrides: Partial<AssistantMessageResponse> = {}): AssistantMessageResponse {
  return {
    reply: 'Under Billing in settings.',
    intent: 'billing',
    confidence: 0.9,
    handler: 'agent',
    route_reason: 'matched',
    fallback: false,
    handled: true,
    policy_modified: false,
    policy_rule: null,
    escalated: false,
    escalation_reasons: [],
    review_id: null,
    citations: [],
    conversation_id: null,
    trace_id: 'abc123',
    ...overrides,
  };
}

/** A client whose methods are stubs, so no component touches the network. */
function stubClient(overrides: Partial<Record<keyof ApiClient, unknown>> = {}) {
  const client = new ApiClient({ fetchImpl: (async () => {
    throw new Error('no test should reach fetch');
  }) as never });
  return Object.assign(client, overrides) as ApiClient;
}

beforeEach(() => {
  window.localStorage.clear();
  resetTurnIds();
});

describe('sending', () => {
  it('shows the question before the server has seen it', async () => {
    let release: (value: AssistantMessageResponse) => void = () => {};
    const pending = new Promise<AssistantMessageResponse>((resolve) => {
      release = resolve;
    });
    const client = stubClient({ sendMessage: vi.fn().mockReturnValue(pending) });
    const { result } = renderHook(() => useConversation(client));

    act(() => {
      void result.current.send('where are my invoices?');
    });

    await waitFor(() => expect(result.current.turns).toHaveLength(1));
    expect(result.current.turns[0]).toMatchObject({
      role: 'customer',
      text: 'where are my invoices?',
      status: 'pending',
    });
    expect(result.current.sending).toBe(true);

    await act(async () => {
      release(reply());
      await pending;
    });

    await waitFor(() => expect(result.current.sending).toBe(false));
    expect(result.current.turns.map((t) => t.role)).toEqual(['customer', 'assistant']);
    expect(result.current.turns[0].status).toBe('sent');
  });

  it('carries the citations and trace id onto the reply', async () => {
    const client = stubClient({
      sendMessage: vi.fn().mockResolvedValue(
        reply({
          citations: [
            {
              document_id: 'd1',
              document_title: 'Refunds',
              ordinal: 0,
              excerpt: 'Refunds take five days.',
              similarity: 0.8,
            },
          ],
          escalated: true,
        }),
      ),
    });
    const { result } = renderHook(() => useConversation(client));

    await act(async () => {
      await result.current.send('how long do refunds take?');
    });

    const assistant = result.current.turns[1];
    expect(assistant.citations).toHaveLength(1);
    expect(assistant.traceId).toBe('abc123');
    expect(assistant.escalated).toBe(true);
  });

  it('keeps a failed question on screen and marks it', async () => {
    // Discarding what somebody typed because the network hiccuped is worse
    // than showing it as undelivered.
    const client = stubClient({
      sendMessage: vi.fn().mockRejectedValue(ApiError.unreachable()),
    });
    const { result } = renderHook(() => useConversation(client));

    await act(async () => {
      await result.current.send('hello');
    });

    expect(result.current.turns).toHaveLength(1);
    expect(result.current.turns[0].status).toBe('failed');
    expect(result.current.turns[0].text).toBe('hello');
    expect(result.current.error).toContain('Could not reach the server');
  });

  it('ignores an empty message', async () => {
    const sendMessage = vi.fn();
    const client = stubClient({ sendMessage });
    const { result } = renderHook(() => useConversation(client));

    await act(async () => {
      await result.current.send('   ');
    });

    expect(sendMessage).not.toHaveBeenCalled();
    expect(result.current.turns).toHaveLength(0);
  });

  it('adopts a conversation the server opened', async () => {
    const client = stubClient({
      sendMessage: vi.fn().mockResolvedValue(reply({ conversation_id: CONVERSATION_ID })),
    });
    const { result } = renderHook(() => useConversation(client));

    await act(async () => {
      await result.current.send('hello');
    });

    expect(result.current.conversationId).toBe(CONVERSATION_ID);
    expect(window.localStorage.getItem(CONVERSATION_STORAGE_KEY)).toBe(CONVERSATION_ID);
  });
});

describe('conversations', () => {
  it('starts one and remembers it', async () => {
    const client = stubClient({
      startConversation: vi.fn().mockResolvedValue({
        id: CONVERSATION_ID,
        customer_id: 'c1',
        created_at: '2026-01-01T00:00:00Z',
      }),
    });
    const { result } = renderHook(() => useConversation(client));

    await act(async () => {
      await result.current.start('person@example.com');
    });

    expect(result.current.conversationId).toBe(CONVERSATION_ID);
    expect(window.localStorage.getItem(CONVERSATION_STORAGE_KEY)).toBe(CONVERSATION_ID);
  });

  it('resumes a stored conversation on mount', async () => {
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, CONVERSATION_ID);
    const getHistory = vi.fn().mockResolvedValue({
      conversation_id: CONVERSATION_ID,
      messages: [
        { position: 0, role: 'customer', content: 'earlier', created_at: 'x' },
        { position: 1, role: 'assistant', content: 'answered', created_at: 'x' },
      ],
    });
    const { result } = renderHook(() => useConversation(stubClient({ getHistory })));

    await waitFor(() => expect(result.current.turns).toHaveLength(2));
    expect(getHistory).toHaveBeenCalledWith(CONVERSATION_ID);
    expect(result.current.turns.map((t) => t.text)).toEqual(['earlier', 'answered']);
    expect(result.current.conversationId).toBe(CONVERSATION_ID);
  });

  it('forgets a stored id the server no longer knows', async () => {
    // A cleared database or a different deployment: a permanent error the
    // user cannot act on would be worse than starting fresh.
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, CONVERSATION_ID);
    const client = stubClient({
      getHistory: vi
        .fn()
        .mockRejectedValue(new ApiError('conversation_not_found', 'Not found.', 404)),
    });
    const { result } = renderHook(() => useConversation(client));

    await waitFor(() =>
      expect(window.localStorage.getItem(CONVERSATION_STORAGE_KEY)).toBeNull(),
    );
    expect(result.current.conversationId).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it('reports a real failure to load history', async () => {
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, CONVERSATION_ID);
    const client = stubClient({
      getHistory: vi.fn().mockRejectedValue(ApiError.unreachable()),
    });
    const { result } = renderHook(() => useConversation(client));

    await waitFor(() => expect(result.current.error).not.toBeNull());
    // The id is kept: the server being unreachable says nothing about it.
    expect(window.localStorage.getItem(CONVERSATION_STORAGE_KEY)).toBe(CONVERSATION_ID);
  });

  it('resets to a fresh conversation', async () => {
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, CONVERSATION_ID);
    const client = stubClient({
      getHistory: vi
        .fn()
        .mockResolvedValue({ conversation_id: CONVERSATION_ID, messages: [] }),
    });
    const { result } = renderHook(() => useConversation(client));
    await waitFor(() => expect(result.current.conversationId).toBe(CONVERSATION_ID));

    act(() => result.current.reset());

    expect(result.current.conversationId).toBeNull();
    expect(result.current.turns).toEqual([]);
    expect(window.localStorage.getItem(CONVERSATION_STORAGE_KEY)).toBeNull();
  });

  it('does nothing on mount without a stored id', async () => {
    const getHistory = vi.fn();
    renderHook(() => useConversation(stubClient({ getHistory })));
    await waitFor(() => expect(getHistory).not.toHaveBeenCalled());
  });
});
