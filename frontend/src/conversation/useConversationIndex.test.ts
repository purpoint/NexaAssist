/**
 * The local index of conversations.
 *
 * What matters here is that it never invents a conversation and never breaks
 * on storage it did not write.
 */

import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import {
  CONVERSATION_INDEX_STORAGE_KEY,
  groupLabel,
  titleFrom,
  UNTITLED,
  useConversationIndex,
} from './useConversationIndex';

beforeEach(() => {
  window.localStorage.clear();
});

describe('remembering', () => {
  it('starts empty', () => {
    const { result } = renderHook(() => useConversationIndex());
    expect(result.current.entries).toEqual([]);
  });

  it('remembers a conversation that was started', () => {
    const { result } = renderHook(() => useConversationIndex());
    act(() => result.current.remember('c1', UNTITLED));
    expect(result.current.entries.map((entry) => entry.id)).toEqual(['c1']);
  });

  it('puts the most recent first', () => {
    const { result } = renderHook(() => useConversationIndex());
    act(() => result.current.remember('older', UNTITLED));
    act(() => result.current.remember('newer', UNTITLED));
    expect(result.current.entries.map((entry) => entry.id)).toEqual(['newer', 'older']);
  });

  it('does not duplicate a conversation it already knows', () => {
    const { result } = renderHook(() => useConversationIndex());
    act(() => result.current.remember('c1', UNTITLED));
    act(() => result.current.remember('c1', UNTITLED));
    expect(result.current.entries).toHaveLength(1);
  });

  it('survives a reload', () => {
    const first = renderHook(() => useConversationIndex());
    act(() => first.result.current.remember('c1', UNTITLED));

    const second = renderHook(() => useConversationIndex());
    expect(second.result.current.entries.map((entry) => entry.id)).toEqual(['c1']);
  });

  it('forgets one when asked', () => {
    const { result } = renderHook(() => useConversationIndex());
    act(() => result.current.remember('c1', UNTITLED));
    act(() => result.current.forget('c1'));
    expect(result.current.entries).toEqual([]);
  });
});

describe('naming', () => {
  it('names a conversation from its first question', () => {
    const { result } = renderHook(() => useConversationIndex());
    act(() => result.current.remember('c1', UNTITLED));
    act(() => result.current.nameIfUnnamed('c1', 'How long does shipping take?'));
    expect(result.current.entries[0].title).toBe('How long does shipping take?');
  });

  it('does not rename it on the second question', () => {
    // The title is a label for finding the conversation again, not a summary
    // that follows the discussion around.
    const { result } = renderHook(() => useConversationIndex());
    act(() => result.current.remember('c1', UNTITLED));
    act(() => result.current.nameIfUnnamed('c1', 'First question'));
    act(() => result.current.nameIfUnnamed('c1', 'Second question'));
    expect(result.current.entries[0].title).toBe('First question');
  });

  it('ignores a conversation it does not know', () => {
    const { result } = renderHook(() => useConversationIndex());
    act(() => result.current.nameIfUnnamed('missing', 'Anything'));
    expect(result.current.entries).toEqual([]);
  });

  it('shortens a long question rather than breaking the layout', () => {
    const long = 'a'.repeat(200);
    expect(titleFrom(long).length).toBeLessThanOrEqual(60);
    expect(titleFrom(long).endsWith('…')).toBe(true);
  });

  it('collapses whitespace, including newlines from a pasted question', () => {
    expect(titleFrom('  How   long\nis  shipping?  ')).toBe('How long is shipping?');
  });
});

describe('storage it did not write', () => {
  it('ignores unparseable storage rather than crashing', () => {
    window.localStorage.setItem(CONVERSATION_INDEX_STORAGE_KEY, 'not json');
    const { result } = renderHook(() => useConversationIndex());
    expect(result.current.entries).toEqual([]);
  });

  it('ignores storage of the wrong shape', () => {
    window.localStorage.setItem(CONVERSATION_INDEX_STORAGE_KEY, '{"id":"c1"}');
    const { result } = renderHook(() => useConversationIndex());
    expect(result.current.entries).toEqual([]);
  });

  it('drops entries missing the fields it needs', () => {
    // Another tab, an older build, or somebody with devtools.
    window.localStorage.setItem(
      CONVERSATION_INDEX_STORAGE_KEY,
      JSON.stringify([{ id: 'good', title: 'Fine', startedAt: 1 }, { id: 'bad' }, null]),
    );
    const { result } = renderHook(() => useConversationIndex());
    expect(result.current.entries.map((entry) => entry.id)).toEqual(['good']);
  });
});

describe('grouping', () => {
  const noon = new Date().setHours(12, 0, 0, 0);

  it('buckets by age', () => {
    expect(groupLabel(noon, noon)).toBe('Today');
    expect(groupLabel(noon - 86_400_000, noon)).toBe('Yesterday');
    expect(groupLabel(noon - 3 * 86_400_000, noon)).toBe('Previous 7 days');
    expect(groupLabel(noon - 30 * 86_400_000, noon)).toBe('Earlier');
  });
});
