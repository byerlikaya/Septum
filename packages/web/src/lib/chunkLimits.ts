/** Largest chunk size, in characters, the settings API accepts. Mirrors MAX_PRE_APPROVED_CHUNK_CHARS; larger values get a 422. */
export const MAX_CHUNK_CHARS = 20000;

/** Smallest text chunk size. Mirrors MIN_CHUNK_SIZE on the API. */
export const MIN_CHUNK_SIZE = 100;

/** Smallest PDF/DOCX chunk size. Mirrors MIN_PDF_CHUNK_SIZE on the API. */
export const MIN_PDF_CHUNK_SIZE = 800;

/** Smallest audio and spreadsheet chunk size. */
export const MIN_OTHER_CHUNK_SIZE = 1;

/** Largest chunk overlap. Mirrors MAX_CHUNK_OVERLAP on the API. */
export const MAX_CHUNK_OVERLAP = MAX_CHUNK_CHARS / 2;

/** Largest overlap the API accepts for a chunk size: at most half of it (chunk_overlap_fits). */
export function maxOverlapFor(chunkSize: number): number {
  return Math.min(MAX_CHUNK_OVERLAP, Math.floor(chunkSize / 2));
}
