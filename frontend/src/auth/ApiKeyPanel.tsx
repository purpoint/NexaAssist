/**
 * Where somebody supplies the API key.
 *
 * Two ways in: the header control, for setting or replacing a key when nothing
 * is wrong, and automatically when a request comes back 401 — because being
 * told "authentication is required" and then having to hunt for where to type
 * the key is a bad way to learn that the deployment is protected.
 *
 * The field is a password input so a key is not readable over a shoulder or in
 * a screen share, with a reveal toggle because a key that cannot be checked
 * gets pasted wrong and retyped blind.
 */

import { useEffect, useId, useRef, useState, type FormEvent } from 'react';

export function ApiKeyPanel({
  configured,
  required,
  onSave,
  onClear,
  onDismiss,
}: {
  configured: boolean;
  /** True when a request was refused for want of a credential. */
  required: boolean;
  onSave: (key: string) => void;
  onClear: () => void;
  onDismiss: () => void;
}) {
  const [value, setValue] = useState('');
  const [revealed, setRevealed] = useState(false);
  const inputId = useId();
  const field = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    // Opened because a request was refused: put the cursor where the fix is.
    if (required) field.current?.focus();
  }, [required]);

  useEffect(() => {
    // Escape closes it, which is what every dismissible panel has taught
    // people to expect -- but not while a request is blocked, where closing
    // would hide the only thing that can unblock it.
    if (required) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onDismiss();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [required, onDismiss]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!value.trim()) return;
    onSave(value.trim());
    setValue('');
    setRevealed(false);
  };

  return (
    <section
      className="apikey"
      aria-labelledby={`${inputId}-heading`}
      // `assertive` only when something is blocked; announcing a panel the
      // user opened themselves would interrupt them for no reason.
      aria-live={required ? 'assertive' : 'off'}
    >
      <h2 className="apikey__heading" id={`${inputId}-heading`}>
        {required ? 'This deployment needs an API key' : 'API access'}
      </h2>
      <p className="apikey__body">
        {required
          ? 'The server refused the request for want of a credential. Paste the key you were issued.'
          : configured
            ? 'A key is stored in this browser and sent with every request.'
            : 'No key is stored. Add one if this deployment requires authentication.'}
      </p>

      <form className="apikey__form" onSubmit={submit}>
        {/* Distinct from the section heading, which is also "API key" --
            two things with one accessible name are two things a screen
            reader user cannot tell apart. */}
        <label className="visually-hidden" htmlFor={inputId}>
          Paste your API key
        </label>
        <input
          id={inputId}
          ref={field}
          className="apikey__input"
          type={revealed ? 'text' : 'password'}
          value={value}
          autoComplete="off"
          spellCheck={false}
          placeholder={configured ? 'Replace the stored key' : 'Paste your key'}
          onChange={(event) => setValue(event.target.value)}
        />
        <button
          type="button"
          className="button button--quiet"
          onClick={() => setRevealed((shown) => !shown)}
        >
          {revealed ? 'Hide' : 'Show'}
        </button>
        <button type="submit" className="button" disabled={value.trim().length === 0}>
          Save
        </button>
      </form>

      <div className="apikey__actions">
        {configured ? (
          <button type="button" className="button button--quiet" onClick={onClear}>
            Forget this key
          </button>
        ) : null}
        {required ? null : (
          <button type="button" className="button button--quiet" onClick={onDismiss}>
            Close
          </button>
        )}
      </div>

      <p className="apikey__note">
        Stored in this browser. Anything with script access to this page can
        read it, so use a key issued for this client rather than a shared
        administrative one.
      </p>
    </section>
  );
}
