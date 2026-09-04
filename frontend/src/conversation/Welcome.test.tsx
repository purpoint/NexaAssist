/** The first screen: what it says, and what a click on it does. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Welcome, WELCOME_PROMPTS } from './Welcome';

describe('explaining the product', () => {
  it('opens with the question a support tool exists to answer', () => {
    render(<Welcome onPrompt={() => {}} />);
    expect(screen.getByText('How can we help?')).toBeInTheDocument();
  });

  it('promises the two things that separate this from a chatbot', () => {
    // Citation and handoff. Both are real capabilities, and the first screen
    // is where somebody learns the product has them.
    render(<Welcome onPrompt={() => {}} />);
    const body = screen.getByText(/Ask about orders/).textContent ?? '';
    expect(body).toMatch(/cites the passage/);
    expect(body).toMatch(/goes to a person/);
  });
});

describe('suggested prompts', () => {
  it('offers one per area the router actually distinguishes', () => {
    render(<Welcome onPrompt={() => {}} />);
    expect(WELCOME_PROMPTS.length).toBeGreaterThanOrEqual(4);
    for (const prompt of WELCOME_PROMPTS) {
      expect(
        screen.getByRole('button', { name: `${prompt.label}: ${prompt.question}` }),
      ).toBeInTheDocument();
    }
  });

  it('fills the composer instead of sending', async () => {
    // A click that silently spends a model call is a click people learn to
    // be afraid of, and seeing the question first is how somebody learns
    // they can edit it.
    const onPrompt = vi.fn();
    render(<Welcome onPrompt={onPrompt} />);
    await userEvent.click(
      screen.getByRole('button', { name: /^Shipping & delivery:/ }),
    );
    expect(onPrompt).toHaveBeenCalledWith('How long does standard shipping take?');
  });

  it('names each prompt without running the two lines together', () => {
    // The label and the question are separate lines; a name computed from
    // the text alone announces "Shipping & deliveryHow long does…".
    render(<Welcome onPrompt={() => {}} />);
    for (const prompt of WELCOME_PROMPTS) {
      const button = screen.getByRole('button', { name: `${prompt.label}: ${prompt.question}` });
      expect(button).toHaveAttribute('aria-label', `${prompt.label}: ${prompt.question}`);
    }
  });
});
