import { MAX_CHAT_DOCUMENTS, capDocumentIds } from "../chatLimits";

describe("chat document cap", () => {
  it("matches the API limit of 50 documents per question", () => {
    expect(MAX_CHAT_DOCUMENTS).toBe(50);
  });

  it("leaves a selection at or under the cap untouched", () => {
    expect(capDocumentIds([1, 2])).toEqual({ ids: [1, 2], trimmed: 0 });
    const atCap = Array.from({ length: 50 }, (_, i) => i + 1);
    expect(capDocumentIds(atCap)).toEqual({ ids: atCap, trimmed: 0 });
  });

  it("keeps the first 50 ids and reports how many were trimmed", () => {
    const ids = Array.from({ length: 57 }, (_, i) => i + 1);
    const result = capDocumentIds(ids);
    expect(result.ids).toHaveLength(50);
    expect(result.ids[49]).toBe(50);
    expect(result.trimmed).toBe(7);
  });
});
