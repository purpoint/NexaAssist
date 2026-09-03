/**
 * The transcript.
 *
 * Every piece of text is rendered as text. No `dangerouslySetInnerHTML`
 * anywhere: a reply is model output and a citation is document content, and
 * both are exactly the kind of thing an attacker would like rendered as
 * markup.
 */

import { useEffect, useRef } from 'react';

import { Spinner } from '../components/primitives';
import { Citations } from './Citations';
import type { Turn } from './model';

export function MessageList({ turns, sending }: { turns: Turn[]; sending: boolean }) {
  const end = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    // Follow the conversation, but never yank a page that is not animating
    // for someone who asked for reduced motion.
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    end.current?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' });
  }, [turns.length, sending]);

  return (
    <ol className="messages" aria-label="Conversation">
      {turns.map((turn) => (
        <li key={turn.id} className={`message message--${turn.role}`}>
          <div className="message__bubble">
            <p className="message__text">{turn.text}</p>
            {turn.role === 'assistant' ? <Citations citations={turn.citations} /> : null}
            {turn.escalated ? (
              <p className="message__note">A support agent has been asked to look.</p>
            ) : null}
          </div>
          {turn.status === 'failed' ? (
            <p className="message__status message__status--failed">Not delivered</p>
          ) : null}
          {turn.status === 'pending' ? (
            <p className="message__status">Sending…</p>
          ) : null}
        </li>
      ))}
      {sending ? (
        <li className="message message--assistant">
          <div className="message__bubble message__bubble--thinking">
            <Spinner label="The assistant is replying" />
          </div>
        </li>
      ) : null}
      <div ref={end} />
    </ol>
  );
}
