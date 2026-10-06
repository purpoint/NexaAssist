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

export function Citations({
  citations,
  showMatch = true,
}: {
  citations: Citation[];
  /**
   * Whether the similarity is worth showing.
   *
   * False under the offline hashing embedder, where a correct retrieval
   * scores around 0.1. Rendering that as "11% match" next to an answer that
   * is right reads as a broken product, and the honest options are to explain
   * the scale or to omit it. The passage reference is what a reader actually
   * checks the claim against; the score was never the point.
   */
  showMatch?: boolean;
}) {
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
              {showMatch ? (
                <span className="source__match">
                  {matchPercent(citation.similarity)} match
                </span>
              ) : null}
            </p>
          </li>
        ))}
      </ul>
    </details>
  );
}
