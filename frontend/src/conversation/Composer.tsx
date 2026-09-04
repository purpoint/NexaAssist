/**
 * Where a question is typed.
 *
 * Enter sends and Shift+Enter makes a newline, which is what every chat
 * interface has taught people to expect. The button stays present for anyone
 * not using a keyboard, and the shortcut is written next to it because a
 * convention is only obvious to people who already know it.
 *
 * The draft is controlled from outside as well as in, so a suggested prompt
 * can fill the box. It fills it rather than sending it: a click that silently
 * spends a model call is a click people learn to be afraid of.
 */

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';

import { SendIcon } from '../components/icons';

export function Composer({
  disabled,
  onSend,
  draft,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
  /**
   * Set by a suggested prompt. The token, not the text, is what marks it as
   * new -- otherwise choosing the same prompt twice would not refill a box
   * the user had since cleared.
   */
  draft?: { text: string; token: number };
}) {
  const [text, setText] = useState('');
  const field = useRef<HTMLTextAreaElement | null>(null);

  const token = draft?.token;
  const drafted = draft?.text;
  useEffect(() => {
    if (token === undefined || !drafted) return;
    setText(drafted);
    // Focus follows the text: the next thing somebody does is edit or send,
    // and both need the cursor to be here.
    field.current?.focus();
    // Keyed on the token alone: the text is read when it fires, and listing
    // it would refill the box on an unrelated re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText('');
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  const empty = text.trim().length === 0;

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <div className="composer__inner">
        <label className="visually-hidden" htmlFor="composer-input">
          Your message
        </label>
        <div className="composer__box">
          <textarea
            id="composer-input"
            ref={field}
            className="composer__input"
            rows={1}
            value={text}
            placeholder="Ask about billing, orders, or your account…"
            disabled={disabled}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button
            type="submit"
            className="button button--primary composer__send"
            disabled={disabled || empty}
            aria-label="Send message"
          >
            <SendIcon />
          </button>
        </div>
        <p className="composer__hint">
          {disabled ? (
            'Waiting for a reply…'
          ) : (
            <>
              <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line
            </>
          )}
        </p>
      </div>
    </form>
  );
}
