/**
 * The API key this browser sends.
 *
 * Held in `localStorage` so it survives a reload, which is the whole point of
 * asking for it once. That is a real trade-off and worth stating plainly:
 * anything with script access to this origin can read it. There is no browser
 * storage that avoids that, and the deeper issue is not the storage — it is
 * that a browser client holds a long-lived shared key at all. Per-user tokens
 * with a short life would be the better model, and are a backend change (M19
 * ships one mechanism, not the only possible one).
 *
 * Given that, two rules the rest of the client keeps: the key is never logged,
 * and it never goes in a URL, where it would reach proxies, history and
 * referrers.
 */

import { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'nexaassist.api_key';

/** Reading storage can throw in a private window; no key is not an error. */
function read(): string | null {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored && stored.trim() ? stored : null;
  } catch {
    return null;
  }
}

function write(key: string | null): void {
  try {
    if (key === null) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, key);
  } catch {
    // A key that cannot be remembered still works for this session.
  }
}

export function useApiKey() {
  // Read once, lazily, so a re-render never re-reads storage.
  const [apiKey, setKey] = useState<string | null>(() => read());

  const save = useCallback((value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    write(trimmed);
    setKey(trimmed);
  }, []);

  const clear = useCallback(() => {
    write(null);
    setKey(null);
  }, []);

  useEffect(() => {
    /**
     * Follow the key across tabs.
     *
     * Signing out in one tab should not leave another still sending the key,
     * and pasting one in should not require reloading every other tab.
     */
    const onStorage = (event: StorageEvent) => {
      if (event.key !== null && event.key !== STORAGE_KEY) return;
      setKey(read());
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  return { apiKey, save, clear, configured: apiKey !== null };
}

export const API_KEY_STORAGE_KEY = STORAGE_KEY;
