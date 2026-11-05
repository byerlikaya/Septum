import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { PdfViewerFrame } from "../PdfViewerFrame";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

const bytes = new ArrayBuffer(8);

function pdfBlob(arrayBuffer: () => Promise<ArrayBuffer> = async () => bytes): Blob {
  return Object.assign(new Blob(["%PDF-1.4"]), { arrayBuffer });
}

function fromFrame(frame: HTMLIFrameElement, data: unknown): void {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data, source: frame.contentWindow }));
  });
}

function mount(page: number | null, pdf: Blob = pdfBlob()) {
  const utils = render(<PdfViewerFrame pdf={pdf} page={page} title="doc" />);
  const frame = screen.getByTitle("doc") as HTMLIFrameElement;
  const post = jest.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => undefined);
  return { ...utils, frame, post };
}

afterEach(cleanup);

it("does not send a page command before the viewer reports loaded", async () => {
  const { frame, post } = mount(4);

  fromFrame(frame, { type: "ready" });
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));

  expect(post.mock.calls.every(([message]) => (message as { type: string }).type !== "page")).toBe(true);
});

it("scrolls to the requested page once loaded, and again when the page changes", () => {
  const { frame, post, rerender } = mount(4);

  fromFrame(frame, { type: "loaded" });
  expect(post).toHaveBeenLastCalledWith({ type: "page", page: 4 }, "*");

  rerender(<PdfViewerFrame pdf={pdfBlob()} page={7} title="doc" />);
  expect(post).toHaveBeenLastCalledWith({ type: "page", page: 7 }, "*");
});

it("sends no page command when no page is requested", () => {
  const { frame, post } = mount(null);

  fromFrame(frame, { type: "loaded" });

  expect(post).not.toHaveBeenCalled();
});

it("ignores malformed viewer messages", async () => {
  const { frame, post } = mount(2);

  for (const data of [null, "ready", { type: "document" }, { type: ["ready"] }, {}]) {
    fromFrame(frame, data);
  }

  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(post).not.toHaveBeenCalled();
  expect(screen.getByRole("status")).toHaveTextContent("preview.document.loading");
});

it("shows the error overlay when the bytes cannot be read", async () => {
  const { frame, post } = mount(
    null,
    pdfBlob(() => Promise.reject(new Error("read failed")))
  );

  fromFrame(frame, { type: "ready" });

  expect(await screen.findByRole("alert")).toHaveTextContent("errors.preview.document");
  expect(post).not.toHaveBeenCalled();
});

it("does not send bytes after unmount", async () => {
  let release: (value: ArrayBuffer) => void = () => undefined;
  const pending = new Promise<ArrayBuffer>((resolve) => {
    release = resolve;
  });
  const { frame, post, unmount } = mount(null, pdfBlob(() => pending));

  fromFrame(frame, { type: "ready" });
  unmount();
  await act(async () => {
    release(bytes);
    await pending;
  });

  expect(post).not.toHaveBeenCalled();
});

it("shows the truncation notice only when pages were capped", () => {
  const { frame } = mount(null);

  fromFrame(frame, { type: "loaded", pageCount: 5, shownPages: 5 });
  expect(screen.queryByText("preview.document.pagesTruncated")).toBeNull();

  fromFrame(frame, { type: "loaded", pageCount: 900, shownPages: 500 });
  expect(screen.getByText("preview.document.pagesTruncated")).toBeTruthy();
});
