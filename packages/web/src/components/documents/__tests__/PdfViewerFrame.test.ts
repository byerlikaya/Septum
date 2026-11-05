import { parseViewerMessage } from "../PdfViewerFrame";

it.each(["ready", "loaded", "error"])("accepts the %s status message", (type) => {
  expect(parseViewerMessage({ type, extra: 1 })).toEqual({ type });
});

it.each([null, undefined, "ready", 42, {}, { type: "document" }, { type: ["ready"] }])(
  "rejects %p",
  (data) => {
    expect(parseViewerMessage(data)).toBeNull();
  }
);

it("carries valid page counts on the loaded message", () => {
  expect(parseViewerMessage({ type: "loaded", pageCount: 900, shownPages: 500 })).toEqual({
    type: "loaded",
    pageCount: 900,
    shownPages: 500
  });
});

it.each([
  { pageCount: "900", shownPages: 500 },
  { pageCount: 900, shownPages: 0 },
  { pageCount: 10.5, shownPages: 5 },
  { pageCount: 10, shownPages: 20 },
  { pageCount: 10 },
  { pageCount: -1, shownPages: -1 }
])("drops malformed page counts %p but still reports loaded", (extra) => {
  expect(parseViewerMessage({ type: "loaded", ...extra })).toEqual({ type: "loaded" });
});
