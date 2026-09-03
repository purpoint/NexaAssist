/** The socket's lifecycle: opening, streaming, failing, and giving up. */

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { parseServerFrame } from './frames';
import { useRealtime, type RealtimeHandlers } from './useRealtime';

const URL = 'ws://test/api/v1/ws';

/** A socket a test drives by hand. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  static readonly OPEN = 1;

  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  receive(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }

  receiveRaw(data: string) {
    this.onmessage?.({ data } as MessageEvent);
  }

  drop() {
    this.readyState = 3;
    this.onclose?.();
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }
}

function handlers(): RealtimeHandlers & {
  deltas: string[];
  completed: Array<[string, string | null]>;
  errors: Array<[string, string]>;
} {
  const deltas: string[] = [];
  const completed: Array<[string, string | null]> = [];
  const errors: Array<[string, string]> = [];
  return {
    deltas,
    completed,
    errors,
    onDelta: (text) => deltas.push(text),
    onComplete: (text, id) => completed.push([text, id]),
    onError: (code, message) => errors.push([code, message]),
  };
}

function mount(sink = handlers()) {
  const view = renderHook(() =>
    useRealtime(URL, sink, { socketFactory: (url) => new FakeSocket(url) as never }),
  );
  return { view, sink };
}

function mountWithTickets(getTicket: () => Promise<string | null>) {
  const sink = handlers();
  const view = renderHook(() =>
    useRealtime(URL, sink, {
      socketFactory: (url) => new FakeSocket(url) as never,
      getTicket,
      urlWithTicket: (base, ticket) => `${base}?ticket=${ticket}`,
    }),
  );
  return { view, sink };
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('frames', () => {
  it('accepts every type the server sends', () => {
    for (const type of ['ready', 'pong', 'delta', 'complete', 'error']) {
      expect(parseServerFrame(JSON.stringify({ type }))?.type).toBe(type);
    }
  });

  it('ignores an unknown type rather than throwing', () => {
    // A server that adds a frame should not break a client that predates it.
    expect(parseServerFrame('{"type":"invented"}')).toBeNull();
    expect(parseServerFrame('not json')).toBeNull();
    expect(parseServerFrame('null')).toBeNull();
  });
});

describe('connecting', () => {
  it('reports open once the socket opens', async () => {
    const { view } = mount();
    expect(view.result.current.ready).toBe(false);

    act(() => FakeSocket.instances[0].open());

    await waitFor(() => expect(view.result.current.ready).toBe(true));
    expect(view.result.current.state).toBe('open');
  });

  it('reconnects after an unexpected close', async () => {
    // No waitFor here: it polls with real timers and would never resolve
    // while they are faked. Every state change below happens inside act,
    // so it is already applied when act returns.
    vi.useFakeTimers();
    const { view } = mount();
    act(() => FakeSocket.instances[0].open());
    expect(view.result.current.state).toBe('open');

    act(() => FakeSocket.instances[0].drop());
    expect(view.result.current.state).toBe('reconnecting');

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(FakeSocket.instances.length).toBe(2);
  });

  it('gives up rather than hammering a server that is not coming back', async () => {
    vi.useFakeTimers();
    const { view } = mount();

    for (let attempt = 0; attempt < 7; attempt += 1) {
      const socket = FakeSocket.instances[FakeSocket.instances.length - 1];
      act(() => socket?.drop());
      await act(async () => {
        vi.advanceTimersByTime(10_000);
      });
    }

    expect(view.result.current.state).toBe('unavailable');
    expect(view.result.current.ready).toBe(false);
    // The cap is what makes the HTTP fallback a decision rather than an
    // accident, and what stops a client hammering an outage.
    expect(FakeSocket.instances.length).toBeLessThanOrEqual(6);
  });
});

describe('asking', () => {
  it('sends the ask frame with the conversation id', async () => {
    const { view } = mount();
    act(() => FakeSocket.instances[0].open());
    await waitFor(() => expect(view.result.current.ready).toBe(true));

    const accepted = view.result.current.ask('why?', 'conv-1');

    expect(accepted).toBe(true);
    expect(JSON.parse(FakeSocket.instances[0].sent[0])).toEqual({
      type: 'ask',
      question: 'why?',
      conversation_id: 'conv-1',
    });
  });

  it('refuses to send when the socket is not open', () => {
    const { view } = mount();
    // Never opened: the caller must fall back rather than lose the question.
    expect(view.result.current.ask('why?', null)).toBe(false);
  });

  it('accepts only one question at a time', async () => {
    const { view } = mount();
    act(() => FakeSocket.instances[0].open());
    await waitFor(() => expect(view.result.current.ready).toBe(true));

    expect(view.result.current.ask('first', null)).toBe(true);
    expect(view.result.current.ask('second', null)).toBe(false);
  });

  it('streams deltas and then completes', async () => {
    const { view, sink } = mount();
    act(() => FakeSocket.instances[0].open());
    await waitFor(() => expect(view.result.current.ready).toBe(true));
    act(() => {
      view.result.current.ask('why?', null);
    });

    act(() => {
      FakeSocket.instances[0].receive({ type: 'delta', text: 'alpha ' });
      FakeSocket.instances[0].receive({ type: 'delta', text: 'beta' });
      FakeSocket.instances[0].receive({
        type: 'complete',
        text: 'alpha beta',
        deltas: 2,
        conversation_id: 'conv-1',
      });
    });

    expect(sink.deltas).toEqual(['alpha ', 'beta']);
    expect(sink.completed).toEqual([['alpha beta', 'conv-1']]);
  });

  it('discards a delta that arrives after the answer finished', async () => {
    // A late frame from an abandoned attempt must not append itself to the
    // next answer.
    const { view, sink } = mount();
    act(() => FakeSocket.instances[0].open());
    await waitFor(() => expect(view.result.current.ready).toBe(true));
    act(() => {
      view.result.current.ask('why?', null);
    });

    act(() => {
      FakeSocket.instances[0].receive({
        type: 'complete',
        text: 'done',
        deltas: 0,
        conversation_id: null,
      });
      FakeSocket.instances[0].receive({ type: 'delta', text: 'stray' });
    });

    expect(sink.deltas).toEqual([]);
    expect(sink.completed).toHaveLength(1);
  });

  it('reports an error frame and frees the connection for the next question', async () => {
    const { view, sink } = mount();
    act(() => FakeSocket.instances[0].open());
    await waitFor(() => expect(view.result.current.ready).toBe(true));
    act(() => {
      view.result.current.ask('why?', null);
    });

    act(() => {
      FakeSocket.instances[0].receive({
        type: 'error',
        code: 'realtime_conversations_unavailable',
        message: 'This connection cannot record conversations.',
      });
    });

    expect(sink.errors[0][0]).toBe('realtime_conversations_unavailable');
    // The next question must be accepted, not blocked by the failed one.
    expect(view.result.current.ask('again', null)).toBe(true);
  });

  it('ignores a malformed frame without tearing down the socket', async () => {
    const { view, sink } = mount();
    act(() => FakeSocket.instances[0].open());
    await waitFor(() => expect(view.result.current.ready).toBe(true));

    act(() => FakeSocket.instances[0].receiveRaw('<html>proxy error</html>'));

    expect(sink.errors).toEqual([]);
    expect(view.result.current.ready).toBe(true);
  });
});

describe('tickets', () => {
  it('spends a ticket on the handshake', async () => {
    const getTicket = vi.fn().mockResolvedValue('ticket-one');
    const { view } = mountWithTickets(getTicket);

    await waitFor(() => expect(FakeSocket.instances).toHaveLength(1));
    expect(FakeSocket.instances[0].url).toBe(`${URL}?ticket=ticket-one`);

    act(() => FakeSocket.instances[0].open());
    await waitFor(() => expect(view.result.current.ready).toBe(true));
  });

  it('mints a fresh ticket for every reconnect', async () => {
    // Tickets are single-use, so replaying the last one would be refused.
    vi.useFakeTimers();
    const getTicket = vi
      .fn()
      .mockResolvedValueOnce('ticket-one')
      .mockResolvedValueOnce('ticket-two');
    mountWithTickets(getTicket);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    act(() => FakeSocket.instances[0].open());
    act(() => FakeSocket.instances[0].drop());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(getTicket).toHaveBeenCalledTimes(2);
    expect(FakeSocket.instances[1].url).toBe(`${URL}?ticket=ticket-two`);
  });

  it('does not open a socket it cannot authenticate', async () => {
    // No key, or a server that will not mint one: opening anyway only earns a
    // close frame, and the caller falls back to HTTP.
    const { view } = mountWithTickets(vi.fn().mockResolvedValue(null));

    await waitFor(() => expect(view.result.current.state).toBe('unavailable'));
    expect(FakeSocket.instances).toHaveLength(0);
    expect(view.result.current.ready).toBe(false);
  });

  it('treats a failed mint as no ticket rather than crashing', async () => {
    const { view } = mountWithTickets(
      vi.fn().mockRejectedValue(new Error('401')),
    );

    await waitFor(() => expect(view.result.current.state).toBe('unavailable'));
    expect(FakeSocket.instances).toHaveLength(0);
  });

  it('never puts a ticket in the url when none is required', async () => {
    const { view } = mount();
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(1));
    expect(FakeSocket.instances[0].url).toBe(URL);
    expect(view.result.current.state).not.toBe('unavailable');
  });
});
