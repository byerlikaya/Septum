import { parseParentMessage } from "../../../../public/pdf-viewer/protocol.mjs";

it("accepts a document command carrying an ArrayBuffer", () => {
  const data = new ArrayBuffer(4);
  expect(parseParentMessage({ type: "document", data, extra: true })).toEqual({ type: "document", data });
});

it("accepts a page command with a positive integer page", () => {
  expect(parseParentMessage({ type: "page", page: 3 })).toEqual({ type: "page", page: 3 });
});

it.each([
  null,
  "document",
  { type: "document", data: "%PDF" },
  { type: "document", data: new Uint8Array(4) },
  { type: "page", page: 0 },
  { type: "page", page: 1.5 },
  { type: "page", page: "2" },
  { type: "navigate", url: "https://example.com" },
])("rejects %p", (data) => {
  expect(parseParentMessage(data)).toBeNull();
});
