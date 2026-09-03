/**
 * Where a question is typed.
 *
 * Enter sends and Shift+Enter makes a newline, which is what every chat
 * interface has taught people to expect. The button stays present for anyone
 * not using a keyboard.
 */

import { useState, type FormEvent, type KeyboardEvent } from 'react';

export function Composer({
  disabled,
  onSend,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState('');

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

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <label className="visually-hidden" htmlFor="composer-input">
        Your message
      </label>
      <textarea
        id="composer-input"
        className="composer__input"
        rows={2}
        value={text}
        placeholder="Ask about billing, your account, or how something works…"
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <button
        type="submit"
        className="button composer__send"
        disabled={disabled || text.trim().length === 0}
      >
        Send
      </button>
    </form>
  );
}
