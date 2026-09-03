/** The transcript renders content as text, never as markup. */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MessageList } from './MessageList';
import type { Turn } from './model';

const INJECTION = '<img src=x onerror="alert(1)">';

function turn(overrides: Partial<Turn> = {}): Turn {
  return {
    id: 't1',
    role: 'assistant',
    text: 'Under Billing in settings.',
    status: 'sent',
    citations: [],
    ...overrides,
  };
}

describe('rendering', () => {
  it('shows both sides of the exchange in order', () => {
    render(
      <MessageList
        turns={[
          turn({ id: 'a', role: 'customer', text: 'where are my invoices?' }),
          turn({ id: 'b', role: 'assistant', text: 'Under Billing.' }),
        ]}
        sending={false}
      />,
    );

    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('where are my invoices?');
    expect(items[1]).toHaveTextContent('Under Billing.');
  });

  it('renders a reply as text, not as markup', () => {
    // A reply is model output and a citation is document content: both are
    // exactly what an attacker would like rendered as HTML.
    const { container } = render(
      <MessageList turns={[turn({ text: INJECTION })]} sending={false} />,
    );

    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(INJECTION)).toBeInTheDocument();
  });

  it('renders a citation excerpt as text too', () => {
    const { container } = render(
      <MessageList
        turns={[
          turn({
            citations: [
              {
                document_id: 'd1',
                document_title: 'Refunds',
                ordinal: 0,
                excerpt: INJECTION,
                similarity: 0.9,
              },
            ],
          }),
        ]}
        sending={false}
      />,
    );

    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(INJECTION)).toBeInTheDocument();
  });

  it('offers sources only when there are some', () => {
    const { rerender } = render(
      <MessageList turns={[turn()]} sending={false} />,
    );
    expect(screen.queryByText(/source/)).toBeNull();

    rerender(
      <MessageList
        turns={[
          turn({
            citations: [
              {
                document_id: 'd1',
                document_title: 'Refunds',
                ordinal: 0,
                excerpt: 'Five days.',
                similarity: 0.9,
              },
            ],
          }),
        ]}
        sending={false}
      />,
    );
    expect(screen.getByText('1 source')).toBeInTheDocument();
  });

  it('marks an undelivered question', () => {
    render(
      <MessageList
        turns={[turn({ role: 'customer', text: 'hello', status: 'failed' })]}
        sending={false}
      />,
    );
    expect(screen.getByText('Not delivered')).toBeInTheDocument();
  });

  it('announces that a reply is on its way', () => {
    render(<MessageList turns={[]} sending />);
    expect(screen.getByRole('status')).toHaveTextContent('The assistant is replying');
  });

  it('says when a person has been brought in', () => {
    render(<MessageList turns={[turn({ escalated: true })]} sending={false} />);
    expect(
      screen.getByText('A support agent has been asked to look.'),
    ).toBeInTheDocument();
  });
});
