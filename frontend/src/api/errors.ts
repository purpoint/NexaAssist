/**
 * What a failed request becomes.
 *
 * One error type for every failure, so a caller writes one `catch`. The
 * backend already promises a single error shape; this preserves it rather
 * than letting a network failure and a 404 arrive as different animals.
 */

import type { ErrorResponse } from './types';

/** Used when the server sent nothing we could parse -- an outage, or a proxy. */
export const UNREACHABLE_CODE = 'network_unreachable';

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details?: unknown;

  constructor(code: string, message: string, status: number, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details;
  }

  /** True when retrying the same request could plausibly succeed. */
  get retryable(): boolean {
    return this.status === 0 || this.status === 429 || this.status >= 500;
  }

  /** True when the caller must authenticate before this can work. */
  get unauthenticated(): boolean {
    return this.status === 401;
  }

  /** True when the caller is asking too often. */
  get rateLimited(): boolean {
    return this.status === 429;
  }

  static fromResponse(status: number, body: unknown): ApiError {
    const parsed = body as Partial<ErrorResponse> | null;
    if (parsed && typeof parsed.code === 'string' && typeof parsed.message === 'string') {
      return new ApiError(parsed.code, parsed.message, status, parsed.details);
    }
    // A body we cannot read is not a reason to show the user raw HTML.
    return new ApiError(
      'unexpected_error',
      'Something went wrong. Please try again.',
      status,
    );
  }

  static unreachable(): ApiError {
    return new ApiError(
      UNREACHABLE_CODE,
      'Could not reach the server. Check your connection and try again.',
      0,
    );
  }
}
