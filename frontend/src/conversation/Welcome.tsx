/**
 * What the screen says before anyone has asked anything.
 *
 * The old empty state said "No messages yet", which is true and useless. This
 * one has to do the work of explaining the product, because it is the first
 * thing anybody sees.
 *
 * The prompts fill the composer rather than sending themselves. A click that
 * silently spends a model call is a click people learn to be afraid of, and
 * seeing the question in the box first is also how someone learns they can
 * edit it.
 */

import { BrandMark } from '../components/icons';

/**
 * Chosen to match the intents the router actually distinguishes, so the
 * prompts demonstrate real routing rather than four variations of one path.
 */
const PROMPTS = [
  {
    label: 'Shipping & delivery',
    question: 'How long does standard shipping take?',
  },
  {
    label: 'Refund policy',
    question: 'What is your refund window?',
  },
  {
    label: 'Account help',
    question: 'How do I reset my password?',
  },
  {
    label: 'Payment issue',
    question: 'My payment was declined — what should I do?',
  },
];

export function Welcome({ onPrompt }: { onPrompt: (question: string) => void }) {
  return (
    <div className="welcome">
      <span className="welcome__mark" aria-hidden="true">
        <BrandMark size={22} />
      </span>
      <h2 className="welcome__title">How can we help?</h2>
      <p className="welcome__body">
        Ask about billing, orders, accounts, shipping, or policies. Answers are
        drawn from your knowledge base and cite the passage they came from.
      </p>
      <ul className="welcome__prompts">
        {PROMPTS.map((prompt) => (
          <li key={prompt.label}>
            <button
              type="button"
              className="prompt"
              // Spelled out, because the two lines otherwise run together
              // into "Shipping & deliveryHow long does shipping take?" when
              // the name is computed from the text.
              aria-label={`${prompt.label}: ${prompt.question}`}
              onClick={() => onPrompt(prompt.question)}
            >
              <span className="prompt__label">{prompt.label}</span>
              <span className="prompt__question">{prompt.question}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export const WELCOME_PROMPTS = PROMPTS;
