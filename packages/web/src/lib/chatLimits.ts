/** Most documents one chat question may reference. Mirrors the API cap (MAX_RETRY_DOCUMENTS); larger requests get a 422. */
export const MAX_CHAT_DOCUMENTS = 50;

export interface CappedDocumentIds {
  ids: number[];
  trimmed: number;
}

export function capDocumentIds(ids: readonly number[]): CappedDocumentIds {
  const kept = ids.slice(0, MAX_CHAT_DOCUMENTS);
  return { ids: kept, trimmed: ids.length - kept.length };
}
