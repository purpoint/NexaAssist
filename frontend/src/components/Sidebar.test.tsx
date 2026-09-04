/** Conversation navigation, and what it honestly claims to be. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { IndexedConversation } from '../conversation/useConversationIndex';
import { Sidebar } from './Sidebar';

const HOUR = 3_600_000;
const DAY = 86_400_000;

function entry(overrides: Partial<IndexedConversation> = {}): IndexedConversation {
  return {
    id: 'c1',
    title: 'How long does shipping take?',
    // Midday today, so the "Today" bucket does not depend on when tests run.
    startedAt: new Date().setHours(12, 0, 0, 0),
    ...overrides,
  };
}

function list(entries: IndexedConversation[], overrides = {}) {
  return (
    <Sidebar
      entries={entries}
      activeId={null}
      open={false}
      onSelect={() => {}}
      onNew={() => {}}
      {...overrides}
    />
  );
}

describe('listing', () => {
  it('invites a first conversation rather than showing an empty box', () => {
    render(list([]));
    expect(screen.getByText('Conversations you start appear here.')).toBeInTheDocument();
  });

  it('shows a conversation by the question that started it', () => {
    // "Conversation 3" tells a reader nothing they can use to find the one
    // they want.
    render(list([entry()]));
    expect(
      screen.getByRole('button', { name: /How long does shipping take\?/ }),
    ).toBeInTheDocument();
  });

  it('groups by age so a long list stays scannable', () => {
    render(
      list([
        entry({ id: 'a', title: 'Today one' }),
        entry({ id: 'b', title: 'Older one', startedAt: Date.now() - 3 * DAY }),
      ]),
    );
    expect(screen.getByText('Today')).toBeInTheDocument();
    expect(screen.getByText('Previous 7 days')).toBeInTheDocument();
  });

  it('marks the open conversation in a way a screen reader can hear', () => {
    // aria-current, not just a tint: the active row must not be colour-only.
    render(list([entry({ id: 'c1' }), entry({ id: 'c2', title: 'Other' })], { activeId: 'c1' }));
    const active = screen.getByRole('button', { name: /How long does shipping take\?/ });
    expect(active).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('button', { name: /Other/ })).not.toHaveAttribute('aria-current');
  });
});

describe('honesty about where the list lives', () => {
  it('says the list is local and the conversations are not', () => {
    // The API cannot enumerate conversations. A sidebar that looked like an
    // account history would mislead somebody about where their data is.
    render(list([entry()]));
    expect(
      screen.getByText(/Saved in this browser\. The conversations themselves live on the server\./),
    ).toBeInTheDocument();
  });
});

describe('navigation', () => {
  it('opens the conversation that was clicked', async () => {
    const onSelect = vi.fn();
    render(list([entry({ id: 'wanted' })], { onSelect }));
    await userEvent.click(screen.getByRole('button', { name: /How long/ }));
    expect(onSelect).toHaveBeenCalledWith('wanted');
  });

  it('starts a new conversation', async () => {
    const onNew = vi.fn();
    render(list([], { onNew }));
    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }));
    expect(onNew).toHaveBeenCalledOnce();
  });

  it('is a navigation landmark with a name', () => {
    render(list([entry()]));
    expect(screen.getByRole('navigation', { name: 'Conversations' })).toBeInTheDocument();
  });
});

describe('time labels', () => {
  it('shows when a conversation started', () => {
    render(list([entry({ startedAt: new Date().setHours(9, 5, 0, 0) })]));
    // Format is the browser's; assert only that a time is rendered.
    expect(screen.getByRole('button', { name: /How long/ }).textContent).toMatch(/\d/);
  });

  it('keeps yesterday separate from today', () => {
    render(list([entry({ startedAt: Date.now() - DAY - HOUR })]));
    expect(screen.getByText('Yesterday')).toBeInTheDocument();
  });
});
