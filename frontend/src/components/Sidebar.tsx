/**
 * Conversation navigation.
 *
 * What it lists is the index this browser keeps, not a server query -- the
 * API has no endpoint that enumerates conversations. The footer says so,
 * because a list that looks like an account history and is not one would
 * mislead somebody about where their data lives.
 */

import { groupLabel, type IndexedConversation } from '../conversation/useConversationIndex';
import { PlusIcon } from './icons';

/** Insertion order is newest-first already, so grouping preserves it. */
function grouped(entries: IndexedConversation[]): [string, IndexedConversation[]][] {
  const groups = new Map<string, IndexedConversation[]>();
  for (const entry of entries) {
    const label = groupLabel(entry.startedAt);
    const bucket = groups.get(label);
    if (bucket) bucket.push(entry);
    else groups.set(label, [entry]);
  }
  return [...groups.entries()];
}

export function Sidebar({
  entries,
  activeId,
  open,
  onSelect,
  onNew,
}: {
  entries: IndexedConversation[];
  activeId: string | null;
  /** Drawer visibility below the layout breakpoint; ignored above it. */
  open: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <nav
      className={`sidebar ${open ? 'sidebar--open' : ''}`.trim()}
      aria-label="Conversations"
    >
      <div>
        <button type="button" className="button sidebar__new" onClick={onNew}>
          <PlusIcon />
          New conversation
        </button>
      </div>

      {entries.length === 0 ? (
        <p className="sidebar__empty">
          Conversations you start appear here.
        </p>
      ) : (
        grouped(entries).map(([label, group]) => (
          <div className="sidebar__group" key={label}>
            <h2 className="sidebar__heading">{label}</h2>
            <ul className="sidebar__list">
              {group.map((entry) => (
                <li key={entry.id}>
                  <button
                    type="button"
                    className={`sidebar__item ${
                      entry.id === activeId ? 'sidebar__item--active' : ''
                    }`.trim()}
                    // The active row is announced, not merely tinted.
                    aria-current={entry.id === activeId ? 'true' : undefined}
                    onClick={() => onSelect(entry.id)}
                  >
                    <span className="sidebar__title">{entry.title}</span>
                    <span className="sidebar__time">
                      {new Date(entry.startedAt).toLocaleTimeString([], {
                        hour: 'numeric',
                        minute: '2-digit',
                      })}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}

      <p className="sidebar__foot">
        Saved in this browser. The conversations themselves live on the server.
      </p>
    </nav>
  );
}
