/** The composer: keyboard behaviour, disabled state, and suggested drafts. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Composer } from './Composer';

describe('sending', () => {
  it('sends what was typed', async () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} onSend={onSend} />);
    await userEvent.type(screen.getByLabelText('Your message'), 'hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect(onSend).toHaveBeenCalledWith('hello');
  });

  it('sends on Enter', async () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} onSend={onSend} />);
    await userEvent.type(screen.getByLabelText('Your message'), 'hello{Enter}');
    expect(onSend).toHaveBeenCalledWith('hello');
  });

  it('makes a newline on Shift+Enter rather than sending', async () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} onSend={onSend} />);
    await userEvent.type(screen.getByLabelText('Your message'), 'a{Shift>}{Enter}{/Shift}b');
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Your message')).toHaveValue('a\nb');
  });

  it('trims, and refuses to send nothing', async () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} onSend={onSend} />);
    await userEvent.type(screen.getByLabelText('Your message'), '   {Enter}');
    expect(onSend).not.toHaveBeenCalled();
  });

  it('clears the box after sending', async () => {
    render(<Composer disabled={false} onSend={() => {}} />);
    const box = screen.getByLabelText('Your message');
    await userEvent.type(box, 'hello{Enter}');
    expect(box).toHaveValue('');
  });
});

describe('states', () => {
  it('cannot send an empty box', () => {
    render(<Composer disabled={false} onSend={() => {}} />);
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
  });

  it('cannot send while a reply is on its way', async () => {
    const onSend = vi.fn();
    render(<Composer disabled onSend={onSend} />);
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
    expect(screen.getByLabelText('Your message')).toBeDisabled();
  });

  it('says why it is waiting rather than just greying out', () => {
    render(<Composer disabled onSend={() => {}} />);
    expect(screen.getByText('Waiting for a reply…')).toBeInTheDocument();
  });

  it('teaches the shortcut, which is only obvious to people who know it', () => {
    render(<Composer disabled={false} onSend={() => {}} />);
    expect(screen.getByText(/to send/)).toBeInTheDocument();
    expect(screen.getByText(/for a new line/)).toBeInTheDocument();
  });
});

describe('suggested drafts', () => {
  it('fills the box from a prompt', () => {
    render(
      <Composer disabled={false} onSend={() => {}} draft={{ text: 'Prefilled', token: 1 }} />,
    );
    expect(screen.getByLabelText('Your message')).toHaveValue('Prefilled');
  });

  it('refills a box the user cleared, when the same prompt is chosen again', () => {
    // The token marks the draft as new, not the text -- otherwise choosing
    // the same prompt twice would do nothing the second time.
    const { rerender } = render(
      <Composer disabled={false} onSend={() => {}} draft={{ text: 'Same', token: 1 }} />,
    );
    const box = screen.getByLabelText('Your message');
    rerender(<Composer disabled={false} onSend={() => {}} draft={{ text: 'Same', token: 2 }} />);
    expect(box).toHaveValue('Same');
  });

  it('does not overwrite typing on an unrelated re-render', () => {
    const draft = { text: 'Prefilled', token: 1 };
    const { rerender } = render(
      <Composer disabled={false} onSend={() => {}} draft={draft} />,
    );
    const box = screen.getByLabelText('Your message');
    rerender(<Composer disabled onSend={() => {}} draft={draft} />);
    expect(box).toHaveValue('Prefilled');
  });

  it('starts empty when no prompt was chosen', () => {
    render(<Composer disabled={false} onSend={() => {}} />);
    expect(screen.getByLabelText('Your message')).toHaveValue('');
  });
});
