/** The key entry panel: what it says, what it hides, and what it sends. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ApiKeyPanel } from './ApiKeyPanel';

const KEY = 'web-app-key-0123456789abcdef';

function renderPanel(overrides: Partial<Parameters<typeof ApiKeyPanel>[0]> = {}) {
  const props = {
    configured: false,
    required: false,
    onSave: vi.fn(),
    onClear: vi.fn(),
    onDismiss: vi.fn(),
    ...overrides,
  };
  render(<ApiKeyPanel {...props} />);
  return props;
}

describe('entry', () => {
  it('hides the key while it is typed', () => {
    // A key readable over a shoulder or in a screen share is a leaked key.
    renderPanel();
    expect(screen.getByLabelText('Paste your API key')).toHaveAttribute('type', 'password');
  });

  it('can reveal it, because a key pasted wrong cannot be checked blind', async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole('button', { name: 'Show' }));

    expect(screen.getByLabelText('Paste your API key')).toHaveAttribute('type', 'text');
  });

  it('saves what was typed', async () => {
    const user = userEvent.setup();
    const props = renderPanel();

    await user.type(screen.getByLabelText('Paste your API key'), KEY);
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(props.onSave).toHaveBeenCalledWith(KEY);
  });

  it('will not save nothing', async () => {
    const user = userEvent.setup();
    const props = renderPanel();

    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.type(screen.getByLabelText('Paste your API key'), '   ');
    expect(props.onSave).not.toHaveBeenCalled();
  });

  it('clears the field after saving, so the key is not left on screen', async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.type(screen.getByLabelText('Paste your API key'), KEY);
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(screen.getByLabelText('Paste your API key')).toHaveValue('');
  });

  it('does not autocomplete or spellcheck a credential', () => {
    renderPanel();
    const field = screen.getByLabelText('Paste your API key');
    expect(field).toHaveAttribute('autocomplete', 'off');
    expect(field).toHaveAttribute('spellcheck', 'false');
  });
});

describe('states', () => {
  it('says when no key is stored', () => {
    renderPanel();
    expect(screen.getByText(/No key is stored/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Forget this key' })).toBeNull();
  });

  it('offers to forget a stored key', async () => {
    const user = userEvent.setup();
    const props = renderPanel({ configured: true });

    expect(screen.getByText(/A key is stored in this browser/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Forget this key' }));

    expect(props.onClear).toHaveBeenCalled();
  });

  it('explains a refusal rather than just failing', () => {
    renderPanel({ required: true });
    expect(screen.getByRole('heading')).toHaveTextContent('needs an API key');
    expect(screen.getByText(/refused the request/)).toBeInTheDocument();
  });

  it('cannot be dismissed while a request is blocked', () => {
    // Closing it would leave somebody stuck with no way back to the fix.
    renderPanel({ required: true });
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
  });

  it('can be dismissed when nothing is wrong', async () => {
    const user = userEvent.setup();
    const props = renderPanel();
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(props.onDismiss).toHaveBeenCalled();
  });

  it('states the storage trade-off rather than implying it is safe', () => {
    renderPanel();
    expect(screen.getByText(/script access to this page can\s+read it/)).toBeInTheDocument();
  });

  it('never renders the stored key', () => {
    // The panel is told whether a key exists, never what it is.
    const { container } = render(
      <ApiKeyPanel
        configured
        required={false}
        onSave={vi.fn()}
        onClear={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );
    expect(container.textContent).not.toContain(KEY);
  });
});
