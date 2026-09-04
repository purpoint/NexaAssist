/**
 * A failure, presented.
 *
 * The heading is ours and says what kind of problem it is; the detail is the
 * server's own message, which the error contract guarantees carries field
 * paths and categories only -- never a stack, a table name, a driver error or
 * a credential. Nothing else from the exception reaches the screen.
 *
 * Retry is offered only where retrying could plausibly work. A button that
 * repeats a request guaranteed to fail the same way teaches people to ignore
 * buttons.
 */

import type { Failure, FailureKind } from '../conversation/useConversation';

const HEADINGS: Record<FailureKind, string> = {
  offline: 'Connection problem',
  unauthenticated: 'Authentication required',
  rate_limited: 'Too many requests',
  server: 'Something went wrong',
  request: 'That request could not be processed',
};

export function ErrorNotice({
  failure,
  onRetry,
}: {
  failure: Failure;
  onRetry?: () => void;
}) {
  return (
    // `alert`, so it is announced without the user hunting for it.
    <div className="notice notice--error" role="alert">
      <div className="notice__text">
        <p className="notice__title">{HEADINGS[failure.kind]}</p>
        <p className="notice__body">{failure.message}</p>
      </div>
      {failure.retryable && onRetry ? (
        <button type="button" className="button button--small" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export const ERROR_HEADINGS = HEADINGS;
