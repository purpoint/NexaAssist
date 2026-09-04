/**
 * The conversations this browser knows about.
 *
 * The API has no endpoint that lists conversations -- it can open one, read
 * one by id, and read its messages, but never enumerate them. So the sidebar
 * cannot show "your conversations" in any server-backed sense, and inventing
 * that would be inventing a feature.
 *
 * What it can honestly show is an index kept here: every conversation this
 * browser started, by id. Each entry is a real conversation on the server and
 * reopening one fetches its actual history, so nothing displayed is
 * fabricated. What is local is the *list*, not the conversations -- which is
 * why the sidebar says so, and why clearing site data loses the index and not
 * the conversations.
 *
 * Titles come from the first question asked, because "Conversation 3" tells a
 * reader nothing they can use to find the one they want.
 */

import { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'nexaassist.conversation_index';

/** Enough to be useful, few enough that the sidebar stays scannable. */
const MAX_ENTRIES = 30;

const TITLE_LIMIT = 60;

export interface IndexedConversation {
  id: string;
  /** The first question asked, trimmed; a fallback until one is asked. */
  title: string;
  /** Epoch milliseconds, local to this browser. */
  startedAt: number;
}

function read(): IndexedConversation[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Validated field by field: this is parsed from storage, which another
    // tab, an older build, or a person with devtools can have written.
    return parsed.filter(
      (entry): entry is IndexedConversation =>
        typeof entry === 'object' &&
        entry !== null &&
        typeof (entry as IndexedConversation).id === 'string' &&
        typeof (entry as IndexedConversation).title === 'string' &&
        typeof (entry as IndexedConversation).startedAt === 'number',
    );
  } catch {
    // Unreadable or unparseable storage is an empty index, never a crash.
    return [];
  }
}

function write(entries: IndexedConversation[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // A private window cannot remember the list. The conversations still work.
  }
}

export function titleFrom(text: string): string {
  const collapsed = text.trim().replace(/\s+/g, ' ');
  if (collapsed.length <= TITLE_LIMIT) return collapsed;
  return `${collapsed.slice(0, TITLE_LIMIT - 1).trimEnd()}…`;
}

/** Coarse buckets, which is all a sidebar needs to be scannable. */
export function groupLabel(startedAt: number, now = Date.now()): string {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const today = start.getTime();
  if (startedAt >= today) return 'Today';
  if (startedAt >= today - 86_400_000) return 'Yesterday';
  if (startedAt >= today - 7 * 86_400_000) return 'Previous 7 days';
  return 'Earlier';
}

export function useConversationIndex() {
  const [entries, setEntries] = useState<IndexedConversation[]>([]);

  // Read on mount rather than in the initial state, so a server-rendered or
  // storage-less environment does not throw during the first render.
  useEffect(() => {
    setEntries(read());
  }, []);

  const remember = useCallback((id: string, title: string) => {
    setEntries((current) => {
      const existing = current.find((entry) => entry.id === id);
      const next: IndexedConversation = existing
        ? { ...existing, title: existing.title || title }
        : { id, title, startedAt: Date.now() };
      const rest = current.filter((entry) => entry.id !== id);
      const updated = [next, ...rest].slice(0, MAX_ENTRIES);
      write(updated);
      return updated;
    });
  }, []);

  /** Name a conversation from its first question, never renaming it after. */
  const nameIfUnnamed = useCallback((id: string, question: string) => {
    setEntries((current) => {
      const existing = current.find((entry) => entry.id === id);
      if (!existing || existing.title !== UNTITLED) return current;
      const updated = current.map((entry) =>
        entry.id === id ? { ...entry, title: titleFrom(question) } : entry,
      );
      write(updated);
      return updated;
    });
  }, []);

  const forget = useCallback((id: string) => {
    setEntries((current) => {
      const updated = current.filter((entry) => entry.id !== id);
      write(updated);
      return updated;
    });
  }, []);

  return { entries, remember, nameIfUnnamed, forget };
}

export const UNTITLED = 'New conversation';
export const CONVERSATION_INDEX_STORAGE_KEY = STORAGE_KEY;
