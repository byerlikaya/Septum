import { MAX_PROGRESS_IDS, chunkIds, fetchProgressInChunks } from "../documentProgress";

const range = (n: number): number[] => Array.from({ length: n }, (_, i) => i + 1);

it("caps one progress request at 200 ids", () => {
  expect(MAX_PROGRESS_IDS).toBe(200);
});

it("splits ids into chunks no larger than the cap, preserving order", () => {
  const chunks = chunkIds(range(450));
  expect(chunks.map((c) => c.length)).toEqual([200, 200, 50]);
  expect(chunks[1][0]).toBe(201);
  expect(chunks[2][49]).toBe(450);
});

it("returns no chunks for an empty list", () => {
  expect(chunkIds([])).toEqual([]);
});

it("sends 450 ids as three requests and merges the results", async () => {
  const fetchChunk = jest.fn(async (chunk: number[]) =>
    Object.fromEntries(chunk.map((id) => [id, id % 100]))
  );

  const merged = await fetchProgressInChunks(range(450), fetchChunk);

  expect(fetchChunk).toHaveBeenCalledTimes(3);
  expect(fetchChunk.mock.calls.map(([chunk]) => chunk.length)).toEqual([200, 200, 50]);
  expect(Object.keys(merged)).toHaveLength(450);
  expect(merged[1]).toBe(1);
  expect(merged[450]).toBe(50);
});

it("propagates a failed chunk so the caller keeps its previous state", async () => {
  const fetchChunk = jest
    .fn()
    .mockResolvedValueOnce({ 1: 10 })
    .mockRejectedValueOnce(new Error("network"));

  await expect(fetchProgressInChunks(range(250), fetchChunk)).rejects.toThrow("network");
});
