/** How a streamed answer reaches the transcript, and what happens when it cannot. */

import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiClient } from '../api/client';
import type { AssistantMessageResponse } from '../api/types';
import { resetTurnIds } from './model';
import { CONVERSATION_STORAGE_KEY, useConversation } from './useConversation';

function stubClient(overrides: Record<string, unknown> = {}) {
  const client = new ApiClient({
    fetchImpl: (async () => {
      throw new Error('no test should reach fetch');
    }) as never,
  });
  return Object.assign(client, overrides) as ApiClient;
}

function httpReply(): AssistantMessageResponse {
  return {
    reply: 'answered over http',
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
    trace_id: 't',
  };
}

beforeEach(() => {
  window.localStorage.clear();
  resetTurnIds();
});

/**
 * What a stream turned out to be. The socket runs the grounded pipeline now,
 * so every completion states whether the answer was sourced.
 */
const prose = { grounded: false, citations: [], escalated: false };
const sourced = {
  grounded: true,
  citations: [
    {
      document_id: 'doc-1',
      document_title: 'Shipping and delivery',
      ordinal: 0,
      excerpt: 'Standard shipping takes 3 to 5 business days.',
      similarity: 0.83,
    },
  ],
  escalated: false,
};

describe('streaming', () => {
  it('fills a reply as deltas arrive and finishes on complete', async () => {
    const client = stubClient({ sendMessage: vi.fn() });
    const { result } = renderHook(() => useConversation(client));

    await act(async () => {
      await result.current.send('why?', { stream: () => true });
    });

    // The placeholder is present and empty, marked as streaming.
    expect(result.current.turns).toHaveLength(2);
    expect(result.current.turns[1]).toMatchObject({ text: '', streaming: true });

    act(() => result.current.appendDelta('alpha '));
    act(() => result.current.appendDelta('beta'));
    expect(result.current.turns[1].text).toBe('alpha beta');

    act(() => result.current.completeStream('alpha beta', 'conv-1', sourced));

    expect(result.current.turns[1]).toMatchObject({
      text: 'alpha beta',
      status: 'sent',
      streaming: false,
      streamed: true,
      // The completion says what the answer was, and the turn keeps it: a
      // sourced answer over the socket is now the ordinary case.
      grounded: true,
      escalated: false,
    });
    expect(result.current.turns[1].citations).toHaveLength(1);
    expect(result.current.turns[0].status).toBe('sent');
    expect(result.current.sending).toBe(false);
  });

  it('does not double the answer when complete repeats the deltas', () => {
    // The complete frame carries the whole answer; appending it to the
    // accumulation would show the reply twice.
    const { result } = renderHook(() => useConversation(stubClient()));

    act(() => {
      void result.current.send('why?', { stream: () => true });
    });
    act(() => result.current.appendDelta('alpha beta'));
    act(() => result.current.completeStream('alpha beta', null, prose));

    expect(result.current.turns[1].text).toBe('alpha beta');
  });

  it('adopts a conversation the socket reports', () => {
    const { result } = renderHook(() => useConversation(stubClient()));
    act(() => {
      void result.current.send('why?', { stream: () => true });
    });
    act(() => result.current.completeStream('done', 'conv-9', prose));

    expect(result.current.conversationId).toBe('conv-9');
    expect(window.localStorage.getItem(CONVERSATION_STORAGE_KEY)).toBe('conv-9');
  });

  it('ignores a delta that arrives with no stream open', () => {
    const { result } = renderHook(() => useConversation(stubClient()));
    act(() => result.current.appendDelta('stray'));
    expect(result.current.turns).toHaveLength(0);
  });

  it('falls back to http when the socket refuses the question', async () => {
    // A closed socket, or a server that will not record: the question must
    // not be lost.
    const sendMessage = vi.fn().mockResolvedValue(httpReply());
    const client = stubClient({ sendMessage });
    const { result } = renderHook(() => useConversation(client));

    await act(async () => {
      await result.current.send('why?', { stream: () => false });
    });

    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(result.current.turns[1].text).toBe('answered over http');
    expect(result.current.turns[1].streamed).toBeUndefined();
  });

  it('never sends the same question over both transports', async () => {
    const sendMessage = vi.fn().mockResolvedValue(httpReply());
    const client = stubClient({ sendMessage });
    const { result } = renderHook(() => useConversation(client));

    await act(async () => {
      await result.current.send('why?', { stream: () => true });
    });

    expect(sendMessage).not.toHaveBeenCalled();
    expect(result.current.turns.filter((t) => t.role === 'customer')).toHaveLength(1);
  });

  it('removes the empty placeholder when the stream fails', async () => {
    const { result } = renderHook(() => useConversation(stubClient()));
    await act(async () => {
      await result.current.send('why?', { stream: () => true });
    });

    act(() => result.current.failStream('This connection cannot record conversations.'));

    // An assistant bubble with no text is worse than no bubble.
    expect(result.current.turns).toHaveLength(1);
    expect(result.current.turns[0]).toMatchObject({ role: 'customer', status: 'failed' });
    expect(result.current.error).toContain('cannot record');
    expect(result.current.sending).toBe(false);
  });

  it('keeps a partial answer that failed midway', async () => {
    const { result } = renderHook(() => useConversation(stubClient()));
    await act(async () => {
      await result.current.send('why?', { stream: () => true });
    });
    act(() => result.current.appendDelta('half an ans'));

    act(() => result.current.failStream('The answer could not be completed.'));

    // Text already shown is not retracted; only the empty case is dropped.
    expect(result.current.turns).toHaveLength(2);
    expect(result.current.turns[1].text).toBe('half an ans');
  });

  it('accepts the next question after a stream ends', async () => {
    const { result } = renderHook(() => useConversation(stubClient()));
    await act(async () => {
      await result.current.send('first', { stream: () => true });
    });
    act(() => result.current.completeStream('one', null, prose));

    await act(async () => {
      await result.current.send('second', { stream: () => true });
    });

    await waitFor(() => expect(result.current.turns).toHaveLength(4));
  });
});

describe('what a streamed answer claims about itself', () => {
  it('keeps the citations a grounded stream arrived with', () => {
    // The reason the socket runs the pipeline at all. Before this, a client
    // that preferred the socket -- which it does whenever one is open -- never
    // saw a source, however well grounded the answer was.
    const { result } = renderHook(() => useConversation(stubClient()));
    act(() => {
      void result.current.send('how long is shipping?', { stream: () => true });
    });
    act(() => result.current.completeStream('3 to 5 days.', null, sourced));

    expect(result.current.turns[1].grounded).toBe(true);
    expect(result.current.turns[1].citations[0].document_title).toBe(
      'Shipping and delivery',
    );
  });

  it('marks an unsourced fallback as unsourced', () => {
    // The server answers in prose when it has no knowledge base. That is a
    // worse answer and has to be shown as one.
    const { result } = renderHook(() => useConversation(stubClient()));
    act(() => {
      void result.current.send('how long is shipping?', { stream: () => true });
    });
    act(() => result.current.completeStream('Usually a few days.', null, prose));

    expect(result.current.turns[1].grounded).toBe(false);
    expect(result.current.turns[1].citations).toEqual([]);
  });

  it('carries an escalation through', () => {
    const { result } = renderHook(() => useConversation(stubClient()));
    act(() => {
      void result.current.send('refund me', { stream: () => true });
    });
    act(() =>
      result.current.completeStream('Passed to an agent.', null, {
        grounded: true,
        citations: [],
        escalated: true,
      }),
    );

    expect(result.current.turns[1].escalated).toBe(true);
  });
});
