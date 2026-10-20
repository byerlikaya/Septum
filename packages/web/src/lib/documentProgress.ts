/** Most document ids one GET /api/documents/progress request may carry (MAX_PROGRESS_IDS on the API; larger requests get a 422). */
export const MAX_PROGRESS_IDS = 200;

export type ProgressMap = Record<number, number>;

export function chunkIds(ids: readonly number[], size: number = MAX_PROGRESS_IDS): number[][] {
  const chunks: number[][] = [];
  for (let i = 0; i < ids.length; i += size) {
    chunks.push(ids.slice(i, i + size));
  }
  return chunks;
}

export async function fetchProgressInChunks(
  ids: readonly number[],
  fetchChunk: (chunk: number[]) => Promise<ProgressMap>,
): Promise<ProgressMap> {
  const results = await Promise.all(chunkIds(ids).map((chunk) => fetchChunk(chunk)));
  return results.reduce<ProgressMap>((merged, part) => ({ ...merged, ...part }), {});
}
