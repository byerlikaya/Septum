"use client";

import type { ReactNode } from "react";
import { getEntityBadgeClasses } from "@/lib/entityColors";

const PLACEHOLDER_RE = /\[([A-Z][A-Z0-9_]*?)_(\d+)\]/g;

export function renderWithPlaceholders(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  const re = new RegExp(PLACEHOLDER_RE.source, "g");

  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const entityType = match[1];
    const classes = getEntityBadgeClasses(entityType);
    parts.push(
      <span
        key={`${match.index}-${match[0]}`}
        data-entity-type={entityType}
        className={`inline-flex items-center rounded-full border px-1.5 py-0 text-[11px] font-medium ${classes}`}
      >
        {match[0]}
      </span>
    );
    lastIndex = re.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts;
}

export function countPlaceholders(text: string): Map<string, number> {
  const counts = new Map<string, number>();
  const re = new RegExp(PLACEHOLDER_RE.source, "g");
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const type = match[1];
    counts.set(type, (counts.get(type) ?? 0) + 1);
  }
  return counts;
}
