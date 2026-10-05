/**
 * Readiness answers two questions: is the backend working, and does it demand
 * a credential. The second exists so the client never has to discover it by
 * being refused.
 */

import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { ApiClient } from '../api/client';
import { useReadiness } from './useReadiness';

function client(components: Record<string, string>, fail = false) {
  return {
    getReadiness: () =>
      fail
        ? Promise.reject(new Error('unreachable'))
        : Promise.resolve({ status: 'ready', database: 'ok', components }),
  } as unknown as ApiClient;
}

describe('backend health', () => {
  it('reports ok when every component is', async () => {
    const { result } = renderHook(() =>
      useReadiness(client({ database: 'ok', authentication: 'not_configured' })),
    );
    await waitFor(() => expect(result.current.connection).toBe('ok'));
  });

  it('reports degraded when one is not', async () => {
    const { result } = renderHook(() =>
      useReadiness(client({ database: 'degraded', authentication: 'ok' })),
    );
    await waitFor(() => expect(result.current.connection).toBe('degraded'));
  });

  it('reports down when the check itself fails', async () => {
    const { result } = renderHook(() => useReadiness(client({}, true)));
    await waitFor(() => expect(result.current.connection).toBe('down'));
  });
});

describe('whether a credential is required', () => {
  it('starts unknown rather than assuming the deployment is open', () => {
    // "We have not asked yet" and "this deployment is open" lead to different
    // decisions. Defaulting to the second opens a socket that may be refused.
    const { result } = renderHook(() =>
      useReadiness(client({ authentication: 'not_configured' })),
    );
    expect(result.current.authEnforced).toBeNull();
  });

  it('says so when the server authenticates', async () => {
    const { result } = renderHook(() =>
      useReadiness(client({ database: 'ok', authentication: 'ok' })),
    );
    await waitFor(() => expect(result.current.authEnforced).toBe(true));
  });

  it('says so when the server does not', async () => {
    const { result } = renderHook(() =>
      useReadiness(client({ database: 'ok', authentication: 'not_configured' })),
    );
    await waitFor(() => expect(result.current.authEnforced).toBe(false));
  });

  it('stays unknown when the backend cannot be reached', async () => {
    // Guessing "open" here is what sends a keyless client at a socket that
    // will refuse it.
    const { result } = renderHook(() => useReadiness(client({}, true)));
    await waitFor(() => expect(result.current.connection).toBe('down'));
    expect(result.current.authEnforced).toBeNull();
  });
});
