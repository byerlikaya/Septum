"use client";

import { useCallback, useState, type Dispatch, type SetStateAction } from "react";
import { MAX_CHAT_DOCUMENTS, capDocumentIds } from "@/lib/chatLimits";

export interface DocumentSelection {
  selectedIds: Set<number>;
  setSelectedIds: Dispatch<SetStateAction<Set<number>>>;
  trimmedCount: number;
}

interface SelectionState {
  ids: Set<number>;
  trimmed: number;
}

/**
 * Chat document selection capped at MAX_CHAT_DOCUMENTS. Every update is
 * clamped inside the state updater, so a restored or auto-added selection
 * that overflows is trimmed (earliest kept) and the number removed is
 * exposed until the next update.
 */
export function useDocumentSelection(): DocumentSelection {
  const [state, setState] = useState<SelectionState>({ ids: new Set(), trimmed: 0 });

  const setSelectedIds = useCallback<Dispatch<SetStateAction<Set<number>>>>((action) => {
    setState((prev) => {
      const next = typeof action === "function" ? action(prev.ids) : action;
      if (next.size <= MAX_CHAT_DOCUMENTS) return { ids: next, trimmed: 0 };
      const capped = capDocumentIds(Array.from(next));
      return { ids: new Set(capped.ids), trimmed: capped.trimmed };
    });
  }, []);

  return { selectedIds: state.ids, setSelectedIds, trimmedCount: state.trimmed };
}
