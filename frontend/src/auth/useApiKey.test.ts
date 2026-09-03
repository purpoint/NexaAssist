/** Storing, replacing, forgetting, and following the key across tabs. */

import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_KEY_STORAGE_KEY, useApiKey } from './useApiKey';

const KEY = 'web-app-key-0123456789abcdef';

beforeEach(() => {
  window.localStorage.clear();
});

describe('storage', () => {
  it('starts with no key', () => {
    const { result } = renderHook(() => useApiKey());
    expect(result.current.apiKey).toBeNull();
    expect(result.current.configured).toBe(false);
  });

  it('saves a key and reports it configured', () => {
    const { result } = renderHook(() => useApiKey());

    act(() => result.current.save(KEY));

    expect(result.current.apiKey).toBe(KEY);
    expect(result.current.configured).toBe(true);
    expect(window.localStorage.getItem(API_KEY_STORAGE_KEY)).toBe(KEY);
  });

  it('reads a stored key on mount', () => {
    window.localStorage.setItem(API_KEY_STORAGE_KEY, KEY);
    const { result } = renderHook(() => useApiKey());
    expect(result.current.apiKey).toBe(KEY);
  });

  it('trims what is pasted', () => {
    // A key copied from a terminal usually arrives with whitespace.
    const { result } = renderHook(() => useApiKey());
    act(() => result.current.save(`  ${KEY}\n`));
    expect(result.current.apiKey).toBe(KEY);
  });

  it('ignores an empty save', () => {
    const { result } = renderHook(() => useApiKey());
    act(() => result.current.save('   '));
    expect(result.current.apiKey).toBeNull();
  });

  it('forgets the key completely', () => {
    window.localStorage.setItem(API_KEY_STORAGE_KEY, KEY);
    const { result } = renderHook(() => useApiKey());

    act(() => result.current.clear());

    expect(result.current.apiKey).toBeNull();
    expect(result.current.configured).toBe(false);
    // Removed, not blanked: a stored empty string would still be a stored key.
    expect(window.localStorage.getItem(API_KEY_STORAGE_KEY)).toBeNull();
  });

  it('treats a blank stored value as no key', () => {
    window.localStorage.setItem(API_KEY_STORAGE_KEY, '   ');
    const { result } = renderHook(() => useApiKey());
    expect(result.current.apiKey).toBeNull();
  });

  it('replaces an existing key', () => {
    window.localStorage.setItem(API_KEY_STORAGE_KEY, 'old-key-0123456789abcdef');
    const { result } = renderHook(() => useApiKey());

    act(() => result.current.save(KEY));

    expect(result.current.apiKey).toBe(KEY);
  });
});

describe('across tabs', () => {
  it('picks up a key saved in another tab', () => {
    const { result } = renderHook(() => useApiKey());

    act(() => {
      window.localStorage.setItem(API_KEY_STORAGE_KEY, KEY);
      window.dispatchEvent(new StorageEvent('storage', { key: API_KEY_STORAGE_KEY }));
    });

    expect(result.current.apiKey).toBe(KEY);
  });

  it('drops a key forgotten in another tab', () => {
    // Signing out in one tab must not leave another still sending the key.
    window.localStorage.setItem(API_KEY_STORAGE_KEY, KEY);
    const { result } = renderHook(() => useApiKey());

    act(() => {
      window.localStorage.removeItem(API_KEY_STORAGE_KEY);
      window.dispatchEvent(new StorageEvent('storage', { key: API_KEY_STORAGE_KEY }));
    });

    expect(result.current.apiKey).toBeNull();
  });

  it('ignores an unrelated storage change', () => {
    window.localStorage.setItem(API_KEY_STORAGE_KEY, KEY);
    const { result } = renderHook(() => useApiKey());

    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', { key: 'nexaassist.conversation_id' }),
      );
    });

    expect(result.current.apiKey).toBe(KEY);
  });
});
