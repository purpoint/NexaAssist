/**
 * The handoff notice.
 *
 * Shown when policy decided a person is needed. It says a person has been
 * *asked*, never that one has replied -- the backend files a review item and
 * says nothing about anybody picking it up, and implying otherwise would be
 * the one lie a support product cannot afford.
 */

export function Escalation() {
  return (
    <div className="handoff" role="note">
      <p className="handoff__title">
        <span className="handoff__dot" aria-hidden="true" />
        Human support requested
      </p>
      <p className="handoff__body">
        This conversation has been handed to a support agent for review. An
        agent will confirm the details with you.
      </p>
    </div>
  );
}
