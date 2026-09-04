/**
 * Saving a conversation against a customer.
 *
 * The behaviour that matters is that this is never in the way: the assistant
 * answers without it, and nothing here should suggest otherwise.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { SaveConversation } from './SaveConversation';

function control(overrides: Partial<Parameters<typeof SaveConversation>[0]> = {}) {
  return (
    <SaveConversation
      saved={false}
      busy={false}
      hasMessages={false}
      onSave={() => {}}
      {...overrides}
    />
  );
}

describe('staying out of the way', () => {
  it('asks for nothing until it is opened', () => {
    // The old design put this field in the middle of the empty screen, where
    // it read as "enter your email to use the assistant".
    render(control());
    expect(screen.queryByLabelText('Customer email')).not.toBeInTheDocument();
  });

  it('is a single quiet control', () => {
    render(control());
    expect(screen.getByRole('button', { name: /Save conversation/ })).toBeInTheDocument();
  });

  it('reports whether it is open', async () => {
    render(control());
    const trigger = screen.getByRole('button', { name: /Save conversation/ });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
  });
});

describe('when opened', () => {
  it('explains what saving does', async () => {
    render(control());
    await userEvent.click(screen.getByRole('button', { name: /Save conversation/ }));
    expect(screen.getByRole('dialog', { name: 'Save this conversation' })).toBeInTheDocument();
    expect(screen.getByText(/reopened later/)).toBeInTheDocument();
  });

  it('says it is optional when nothing has been asked yet', async () => {
    render(control({ hasMessages: false }));
    await userEvent.click(screen.getByRole('button', { name: /Save conversation/ }));
    expect(screen.getByText(/the assistant answers either way/)).toBeInTheDocument();
  });

  it('admits it cannot reach messages already on screen', async () => {
    // A conversation has to exist before messages can be recorded against
    // it. Saying "saved" without that caveat promises a transcript that does
    // not exist.
    render(control({ hasMessages: true }));
    await userEvent.click(screen.getByRole('button', { name: /Save conversation/ }));
    expect(screen.getByText(/stay in this session/)).toBeInTheDocument();
  });

  it('saves the email it was given', async () => {
    const onSave = vi.fn();
    render(control({ onSave }));
    await userEvent.click(screen.getByRole('button', { name: /Save conversation/ }));
    await userEvent.type(screen.getByLabelText('Customer email'), 'a@example.com');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onSave).toHaveBeenCalledWith('a@example.com');
  });

  it('will not save an empty address', async () => {
    render(control());
    await userEvent.click(screen.getByRole('button', { name: /Save conversation/ }));
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('will not save twice while the first is in flight', async () => {
    render(control({ busy: true }));
    await userEvent.click(screen.getByRole('button', { name: /Save conversation/ }));
    await userEvent.type(screen.getByLabelText('Customer email'), 'a@example.com');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('closes on Escape', async () => {
    render(control());
    await userEvent.click(screen.getByRole('button', { name: /Save conversation/ }));
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes on the close button', async () => {
    render(control());
    await userEvent.click(screen.getByRole('button', { name: /Save conversation/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('once saved', () => {
  it('states it plainly and offers no form', () => {
    render(control({ saved: true }));
    expect(screen.getByText('Saved to your account')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Save conversation/ })).not.toBeInTheDocument();
  });
});

describe('dismissing by pointer', () => {
  it('closes when the click lands outside it', async () => {
    // Escape is the keyboard escape hatch; clicking away is what everybody
    // else reaches for.
    render(
      <>
        <SaveConversation saved={false} busy={false} hasMessages={false} onSave={() => {}} />
        <button type="button">elsewhere</button>
      </>,
    );
    await userEvent.click(screen.getByRole('button', { name: /Save conversation/ }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'elsewhere' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('stays open when the click lands inside it', async () => {
    render(
      <SaveConversation saved={false} busy={false} hasMessages={false} onSave={() => {}} />,
    );
    await userEvent.click(screen.getByRole('button', { name: /Save conversation/ }));
    await userEvent.click(screen.getByLabelText('Customer email'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
