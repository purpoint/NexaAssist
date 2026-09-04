/**
 * Associating a conversation with a customer.
 *
 * This used to be a bordered box in the middle of the empty screen, above
 * the composer, asking for an email. It read as a gate -- "enter your email
 * to use the assistant" -- which is the opposite of true: the backend answers
 * a message with no conversation at all. Somebody who has to understand
 * conversation persistence before they can ask a question will not ask one.
 *
 * So it is a control in the header now, opened deliberately, and it explains
 * itself when opened rather than sitting there looking mandatory.
 *
 * One thing it must be honest about: a conversation has to exist before
 * messages can be recorded against it, so saving part-way through captures
 * what follows and not what came before. Saying "saved" without that caveat
 * would promise a transcript that does not exist.
 */

import { useEffect, useId, useRef, useState, type FormEvent } from 'react';

import { CloseIcon, UserIcon } from '../components/icons';

export function SaveConversation({
  saved,
  busy,
  hasMessages,
  onSave,
}: {
  /** True once a conversation exists on the server. */
  saved: boolean;
  busy: boolean;
  /** True when this session already has messages the save cannot reach. */
  hasMessages: boolean;
  onSave: (email: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const fieldId = useId();
  const field = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (open) field.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open]);

  if (saved) {
    return (
      <p className="saved" title="This conversation is being recorded on the server.">
        <UserIcon size={14} />
        Saved to your account
      </p>
    );
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed) return;
    onSave(trimmed);
    setEmail('');
    setOpen(false);
  };

  return (
    <div className="save">
      <button
        type="button"
        className="button button--quiet button--small"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <UserIcon size={14} />
        Save conversation
      </button>

      {open ? (
        <div className="save__panel" role="dialog" aria-label="Save this conversation">
          <div className="save__head">
            <p className="save__title">Save this conversation</p>
            <button
              type="button"
              className="button button--quiet button--small save__close"
              aria-label="Close"
              onClick={() => setOpen(false)}
            >
              <CloseIcon size={14} />
            </button>
          </div>
          <p className="save__body">
            Keeps the exchange against a customer so it can be reopened later.
            {hasMessages
              ? ' Messages from here on are saved; the ones already on screen stay in this session.'
              : ' Optional — the assistant answers either way.'}
          </p>
          <form className="save__form" onSubmit={submit}>
            <label className="visually-hidden" htmlFor={fieldId}>
              Customer email
            </label>
            <input
              id={fieldId}
              ref={field}
              className="field"
              type="email"
              value={email}
              placeholder="customer@example.com"
              onChange={(event) => setEmail(event.target.value)}
            />
            <button
              type="submit"
              className="button button--primary"
              disabled={busy || email.trim().length === 0}
            >
              Save
            </button>
          </form>
        </div>
      ) : null}
    </div>
  );
}
