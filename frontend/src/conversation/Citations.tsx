/**
 * The sources behind an answer.
 *
 * This is the element that distinguishes NexaAssist from a chatbot, so it is
 * built to be read rather than tucked away -- but still collapsed by default,
 * because sources matter when somebody doubts the answer and expanding them
 * by default buries the answer itself.
 *
 * Only fields the backend actually sends appear here: title, excerpt,
 * position and similarity. There is no page number and no document type in
 * the contract, and inventing either would make provenance less trustworthy,
 * not more.
 *
 * Absent entirely when there are none, which includes every reply policy
 * rewrote -- the backend drops citations there rather than attributing text it
 * did not produce.
 */

import { DocumentIcon } from '../components/icons';
import type { Citation } from '../api/types';

/**
 * Similarity is a retrieval score, not a confidence in the answer, so it is
 * labelled "match" rather than anything that sounds like certainty.
 */
function matchPercent(similarity: number): string {
  return `${Math.round(similarity * 100)}%`;
}

export function Citations({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;

  return (
    <details className="sources">
      <summary className="sources__summary">
        <span className="sources__label">
          Sources
          <span className="sources__count">{citations.length}</span>
        </span>
        <span className="sources__hint" aria-hidden="true">
          Grounded in your knowledge base
        </span>
      </summary>
      <ul className="sources__list">
        {citations.map((citation) => (
          <li key={`${citation.document_id}-${citation.ordinal}`} className="source">
            <p className="source__title">
              <DocumentIcon size={14} />
              {citation.document_title}
            </p>
            {/* Plain text, never markup: this is document content, and
                rendering it as HTML would be an injection route. */}
            <blockquote className="source__excerpt">{citation.excerpt}</blockquote>
            <p className="source__meta">
              <span>Passage {citation.ordinal + 1}</span>
              <span className="source__match">{matchPercent(citation.similarity)} match</span>
            </p>
          </li>
        ))}
      </ul>
    </details>
  );
}
