import type { Chunk, EntityDetection } from "@/lib/types";

export interface PreviewChunkLayout<C extends PreviewLayoutChunk = Chunk> {
  chunk: C;
  text: string;
  separator: string;
  detections: EntityDetection[];
}

export type PreviewLayoutChunk = Pick<
  Chunk,
  "id" | "index" | "sanitized_text" | "raw_text" | "start_char" | "end_char"
>;

export interface PreviewLayout<C extends PreviewLayoutChunk = Chunk> {
  chunks: PreviewChunkLayout<C>[];
  /** Detections to show once: those lying wholly in a chunk's overlap
   *  with the previous chunk are dropped; ones crossing its edge are kept. Original (chunk-relative) offsets. */
  visibleDetections: EntityDetection[];
  text: string;
}

const GAP_SEPARATOR = "\n\n";

/**
 * Text the local preview shows for a chunk. Prefers the raw extracted text:
 * entity offsets are relative to it, so masked text would misalign the
 * highlights and defeat verifying what was detected. The preview never
 * leaves the air-gapped zone. Falls back to ``sanitized_text`` for chunks
 * ingested before ``raw_text`` existed.
 */
export function chunkPreviewText(chunk: {
  raw_text?: string | null;
  sanitized_text: string;
}): string {
  return chunk.raw_text ?? chunk.sanitized_text;
}

function hasKnownOffsets(chunk: PreviewLayoutChunk): boolean {
  return (
    chunk.raw_text != null &&
    typeof chunk.start_char === "number" &&
    typeof chunk.end_char === "number"
  );
}

export function buildPreviewLayout<C extends PreviewLayoutChunk>(
  chunks: readonly C[],
  detections: readonly EntityDetection[]
): PreviewLayout<C> {
  const sorted = [...chunks].sort((a, b) => a.index - b.index);
  const byChunk = new Map<number, EntityDetection[]>();
  for (const det of detections) {
    const list = byChunk.get(det.chunk_id);
    if (list) list.push(det);
    else byChunk.set(det.chunk_id, [det]);
  }

  const trimmable = sorted.every(hasKnownOffsets);
  const layouts: PreviewChunkLayout<C>[] = [];
  const dropped = new Set<number>();
  let prevEnd: number | null = null;

  sorted.forEach((chunk, idx) => {
    const full = chunkPreviewText(chunk);
    const own = byChunk.get(chunk.id) ?? [];

    if (!trimmable) {
      layouts.push({
        chunk,
        text: full,
        separator: idx > 0 ? GAP_SEPARATOR : "",
        detections: own,
      });
      return;
    }

    const start = chunk.start_char as number;
    const end = chunk.end_char as number;
    let skip = 0;
    let separator = "";
    if (prevEnd !== null) {
      skip = start < prevEnd ? Math.min(prevEnd - start, full.length) : 0;
      separator = start <= prevEnd ? "" : GAP_SEPARATOR;
    }

    const kept: EntityDetection[] = [];
    for (const det of own) {
      if (det.end_offset <= skip) {
        dropped.add(det.id);
      } else {
        kept.push(
          skip === 0
            ? det
            : {
                ...det,
                start_offset: Math.max(det.start_offset - skip, 0),
                end_offset: det.end_offset - skip,
              }
        );
      }
    }

    layouts.push({
      chunk,
      text: full.slice(skip),
      separator,
      detections: kept,
    });
    prevEnd = prevEnd === null ? end : Math.max(prevEnd, end);
  });

  return {
    chunks: layouts,
    visibleDetections: dropped.size
      ? detections.filter((d) => !dropped.has(d.id))
      : [...detections],
    text: layouts.map((l) => l.separator + l.text).join(""),
  };
}
