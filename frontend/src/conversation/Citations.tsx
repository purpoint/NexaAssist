/**
 * The sources behind an answer.
 *
 * Collapsed by default: they matter when somebody doubts the answer, and
 * expanding them by default buries the answer itself. Absent entirely when
 * there are none, which includes every reply policy rewrote -- the backend
 * drops citations there rather than attributing text it did not produce.
 */

import type { Citation } from '../api/types';

export function Citations({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;

  return (
    <details className="citations">
      <summary className="citations__summary">
        {citations.length} {citations.length === 1 ? 'source' : 'sources'}
      </summary>
      <ul className="citations__list">
        {citations.map((citation) => (
          <li key={`${citation.document_id}-${citation.ordinal}`} className="citation">
            <p className="citation__title">{citation.document_title}</p>
            {/* Plain text, never markup: this is document content, and
                rendering it as HTML would be an injection route. */}
            <p className="citation__excerpt">{citation.excerpt}</p>
            <p className="citation__meta">
              Passage {citation.ordinal + 1} · similarity{' '}
              {citation.similarity.toFixed(2)}
            </p>
          </li>
        ))}
      </ul>
    </details>
  );
}
