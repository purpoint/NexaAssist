/** Failures, as a reader sees them. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { Failure } from '../conversation/useConversation';
import { ErrorNotice } from './ErrorNotice';

function failure(overrides: Partial<Failure> = {}): Failure {
  return { kind: 'server', message: 'Something went wrong.', retryable: true, ...overrides };
}

describe('naming the problem', () => {
  it.each([
    ['offline', 'Connection problem'],
    ['unauthenticated', 'Authentication required'],
    ['rate_limited', 'Too many requests'],
    ['server', 'Something went wrong'],
    ['request', 'That request could not be processed'],
  ] as const)('heads a %s failure with its own words', (kind, heading) => {
    render(<ErrorNotice failure={failure({ kind })} />);
    expect(screen.getByText(heading)).toBeInTheDocument();
  });

  it('shows the server message as the detail', () => {
    render(<ErrorNotice failure={failure({ message: 'Rate limit exceeded.' })} />);
    expect(screen.getByText('Rate limit exceeded.')).toBeInTheDocument();
  });

  it('announces itself rather than waiting to be found', () => {
    render(<ErrorNotice failure={failure()} />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});

describe('retrying', () => {
  it('offers a retry when one could work', async () => {
    const onRetry = vi.fn();
    render(<ErrorNotice failure={failure({ retryable: true })} onRetry={onRetry} />);
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('offers none when the request would fail the same way', () => {
    // A button that repeats a guaranteed failure teaches people to ignore
    // buttons.
    render(<ErrorNotice failure={failure({ retryable: false })} onRetry={() => {}} />);
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });

  it('offers none when the caller has nothing to repeat', () => {
    render(<ErrorNotice failure={failure({ retryable: true })} />);
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });
});

describe('what never reaches the screen', () => {
  it('renders the message as text, not as markup', () => {
    // A server message is still text from outside this process.
    const { container } = render(
      <ErrorNotice failure={failure({ message: '<img src=x onerror="alert(1)">' })} />,
    );
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
  });

  it('shows nothing but the heading and the message', () => {
    // No status code, no error class, no stack: the contract promises the
    // message is safe and nothing else from the failure is.
    render(<ErrorNotice failure={failure({ kind: 'server', message: 'Try later.' })} />);
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toBe('Something went wrongTry later.');
  });
});
