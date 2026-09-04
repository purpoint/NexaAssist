/**
 * The transcript.
 *
 * The two roles are shaped differently on purpose. A question is short and
 * belongs to the person who asked it, so it stays a compact bubble on the
 * right. An answer is the product's output and often carries sources and a
 * handoff notice with it, so it is set as a document -- full width, with an
 * attributed byline -- rather than squeezed into a chat bubble. Everything
 * that qualifies the answer hangs off that block instead of floating loose.
 *
 * Every piece of text is rendered as text. No `dangerouslySetInnerHTML`
 * anywhere: a reply is model output and a citation is document content, and
 * both are exactly the kind of thing an attacker would like rendered as
 * markup.
 */

import { useEffect, useRef } from 'react';

import { BrandMark } from '../components/icons';
import { Citations } from './Citations';
import { Escalation } from './Escalation';
import type { Turn } from './model';

function Byline() {
  return (
    <p className="answer__byline">
      <span className="answer__mark" aria-hidden="true">
        <BrandMark size={12} />
      </span>
      NexaAssist
    </p>
  );
}

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
      {turns.map((turn) =>
        turn.role === 'customer' ? (
          <li key={turn.id} className="message message--customer">
            <div className="question">
              <p className="message__text">{turn.text}</p>
            </div>
            {turn.status === 'failed' ? (
              <p className="message__status message__status--failed">Not delivered</p>
            ) : null}
            {turn.status === 'pending' ? <p className="message__status">Sending…</p> : null}
          </li>
        ) : (
          <li key={turn.id} className="message message--assistant">
            <article className="answer">
              <Byline />
              <p className="message__text answer__text">
                {turn.text}
                {turn.streaming ? <span className="caret" aria-hidden="true" /> : null}
              </p>

              <Citations citations={turn.citations} />

              {turn.escalated ? <Escalation /> : null}

              {turn.streamed && !turn.streaming && !turn.grounded ? (
                // Only for answers that really are unsourced. The socket runs
                // the grounded pipeline now, and falls back to prose only when
                // the server has no knowledge base -- so this marks that
                // fallback rather than every streamed reply. An absence of
                // sources must not read as "none were needed".
                <p className="message__note message__note--quiet">
                  Unsourced reply — the knowledge base was not available.
                </p>
              ) : null}
            </article>
          </li>
        ),
      )}

      {sending && !turns.some((turn) => turn.streaming) ? (
        <li className="message message--assistant">
          <article className="answer answer--thinking">
            <Byline />
            {/* A named state, not a bare spinner: "generating" and
                "reconnecting" are different waits and should not look alike. */}
            <p className="thinking" role="status">
              <span className="thinking__dots" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              Generating response
            </p>
          </article>
        </li>
      ) : null}

      <div ref={end} />
    </ol>
  );
}
