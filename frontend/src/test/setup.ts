/** Shared test setup: DOM matchers, storage, and cleanup between tests. */

import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach } from 'vitest';

/**
 * A working `localStorage`.
 *
 * The jsdom build vitest resolves here exposes `window.localStorage` as a bare
 * object with no methods, so anything that stores a value throws. The
 * application already tolerates storage being unavailable -- every access is
 * guarded -- but a test that exercised only that guard would be testing the
 * fallback and never the behaviour, so the tests get a real implementation.
 */
class MemoryStorage implements Storage {
  private entries = new Map<string, string>();

  get length(): number {
    return this.entries.size;
  }

  clear(): void {
    this.entries.clear();
  }

  getItem(key: string): string | null {
    return this.entries.has(key) ? (this.entries.get(key) as string) : null;
  }

  key(index: number): string | null {
    return Array.from(this.entries.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.entries.delete(key);
  }

  setItem(key: string, value: string): void {
    this.entries.set(key, String(value));
  }
}

/**
 * jsdom implements no layout, so `scrollIntoView` does not exist on an
 * element. The component calls it to follow the conversation; stubbing it
 * here keeps that behaviour in the component rather than weakening it with a
 * guard against a function every real browser has.
 */
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {
    /* no layout to scroll */
  };
}

beforeEach(() => {
  Object.defineProperty(window, 'localStorage', {
    value: new MemoryStorage(),
    configurable: true,
    writable: true,
  });
});

// Without this a component from one test is still mounted during the next,
// and a query that should find one element finds two.
afterEach(cleanup);
