import { buildPreviewLayout } from "../previewLayout";
import type { Chunk, EntityDetection } from "../types";

function chunk(id: number, raw: string | null, start: number | null, end: number | null): Chunk {
  return {
    id,
    document_id: 1,
    index: id,
    sanitized_text: `masked-${id}`,
    raw_text: raw,
    start_char: start,
    end_char: end,
    char_count: raw?.length ?? 0,
    source_page: null,
    source_slide: null,
    source_sheet: null,
    source_timestamp_start: null,
    source_timestamp_end: null,
    section_title: null,
  };
}

function det(id: number, chunkId: number, start: number, end: number): EntityDetection {
  return {
    id,
    chunk_id: chunkId,
    entity_type: "EMAIL",
    placeholder: `[E${id}]`,
    start_offset: start,
    end_offset: end,
    score: 0.9,
  };
}

describe("buildPreviewLayout", () => {
  const joined = "abcdefghijklmnopqrstuvwxyz";

  it("trims overlapping text and joins without a separator", () => {
    const layout = buildPreviewLayout(
      [chunk(1, joined.slice(0, 10), 0, 10), chunk(2, joined.slice(6, 16), 6, 16)],
      []
    );
    expect(layout.chunks.map((c) => c.text)).toEqual(["abcdefghij", "klmnop"]);
    expect(layout.chunks[1].separator).toBe("");
    expect(layout.text).toBe(joined.slice(0, 16));
  });

  it("joins contiguous chunks without a separator", () => {
    const layout = buildPreviewLayout(
      [chunk(1, "abcde", 0, 5), chunk(2, "fghij", 5, 10)],
      []
    );
    expect(layout.text).toBe("abcdefghij");
  });

  it("uses a blank line for a gap", () => {
    const layout = buildPreviewLayout(
      [chunk(1, "abcde", 0, 5), chunk(2, "klmno", 10, 15)],
      []
    );
    expect(layout.chunks[1].separator).toBe("\n\n");
    expect(layout.text).toBe("abcde\n\nklmno");
  });

  it("shows nothing for a chunk fully inside the previous one", () => {
    const layout = buildPreviewLayout(
      [chunk(1, joined.slice(0, 10), 0, 10), chunk(2, joined.slice(2, 6), 2, 6), chunk(3, joined.slice(8, 14), 8, 14)],
      [det(1, 2, 0, 2)]
    );
    expect(layout.chunks[1].text).toBe("");
    expect(layout.chunks[1].separator).toBe("");
    expect(layout.chunks[1].detections).toEqual([]);
    expect(layout.visibleDetections).toEqual([]);
    expect(layout.text).toBe(joined.slice(0, 14));
  });

  it("falls back to untrimmed text when any chunk lacks offsets", () => {
    const layout = buildPreviewLayout(
      [chunk(1, "abcdef", 0, 6), chunk(2, "defghi", null, null)],
      [det(1, 2, 0, 3)]
    );
    expect(layout.text).toBe("abcdef\n\ndefghi");
    expect(layout.chunks[1].detections).toEqual([det(1, 2, 0, 3)]);
    expect(layout.visibleDetections).toHaveLength(1);
  });

  it("falls back when a chunk has no raw_text", () => {
    const layout = buildPreviewLayout(
      [chunk(1, "abcdef", 0, 6), chunk(2, null, 3, 9)],
      []
    );
    expect(layout.text).toBe("abcdef\n\nmasked-2");
  });

  it("drops detections starting in the overlap and shifts the rest", () => {
    const layout = buildPreviewLayout(
      [chunk(1, joined.slice(0, 10), 0, 10), chunk(2, joined.slice(6, 16), 6, 16)],
      [det(1, 1, 7, 9), det(2, 2, 1, 3), det(3, 2, 6, 8)]
    );
    expect(layout.chunks[0].detections.map((d) => d.id)).toEqual([1]);
    expect(layout.chunks[1].detections).toEqual([det(3, 2, 2, 4)]);
    expect(layout.visibleDetections.map((d) => d.id)).toEqual([1, 3]);
    expect(layout.visibleDetections[1].start_offset).toBe(6);
    const shifted = layout.chunks[1].detections[0];
    expect(layout.chunks[1].text.slice(shifted.start_offset, shifted.end_offset)).toBe("mn");
  });

  it("keeps an entity crossing the overlap edge, clipped to the visible part", () => {
    const layout = buildPreviewLayout(
      [chunk(1, "abcdefghij", 0, 10), chunk(2, "ghijklmnop", 6, 16)],
      [det(1, 1, 8, 10), det(2, 2, 2, 6)]
    );
    expect(layout.chunks[0].detections.map((d) => d.id)).toEqual([1]);
    const clipped = layout.chunks[1].detections[0];
    expect(clipped).toEqual(det(2, 2, 0, 2));
    expect(layout.chunks[1].text.slice(clipped.start_offset, clipped.end_offset)).toBe("kl");
    expect(layout.visibleDetections).toEqual([det(1, 1, 8, 10), det(2, 2, 2, 6)]);
  });

  it("orders chunks by index", () => {
    const layout = buildPreviewLayout(
      [chunk(2, "fghij", 5, 10), chunk(1, "abcde", 0, 5)],
      []
    );
    expect(layout.text).toBe("abcdefghij");
  });
});
