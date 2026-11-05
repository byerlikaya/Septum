"use client";

import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import { useI18n } from "@/lib/i18n";

export const PDF_VIEWER_PATH = "/pdf-viewer/index.html";

type ViewerMessage =
  | { type: "ready" }
  | { type: "loaded"; pageCount?: number; shownPages?: number }
  | { type: "error" };

const isCount = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 1;

/** Returns the viewer status message carried by ``data``, or null if it is not one. */
export function parseViewerMessage(data: unknown): ViewerMessage | null {
  if (typeof data !== "object" || data === null) return null;
  const { type, pageCount, shownPages } = data as {
    type?: unknown;
    pageCount?: unknown;
    shownPages?: unknown;
  };
  if (type === "ready" || type === "error") return { type };
  if (type !== "loaded") return null;
  if (isCount(pageCount) && isCount(shownPages) && shownPages <= pageCount) {
    return { type, pageCount, shownPages };
  }
  return { type };
}

type PdfViewerFrameProps = {
  pdf: Blob;
  page: number | null;
  title: string;
};

/**
 * Renders a PDF with the bundled viewer in an opaque-origin sandbox: the frame
 * runs scripts but cannot reach this app's DOM, storage or credentials, so a
 * renderer bug triggered by a hostile PDF stays contained. Bytes and page
 * jumps go in by postMessage to that specific frame.
 */
export function PdfViewerFrame({ pdf, page, title }: Readonly<PdfViewerFrameProps>): ReactElement {
  const t = useI18n();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [status, setStatus] = useState<"loading" | "loaded" | "error">("loading");
  const [pages, setPages] = useState<{ pageCount: number; shownPages: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const onMessage = (event: MessageEvent) => {
      const frameWindow = frameRef.current?.contentWindow;
      if (!frameWindow || event.source !== frameWindow) return;
      const message = parseViewerMessage(event.data);
      if (message?.type === "ready") {
        pdf.arrayBuffer().then(
          (data) => {
            // Sandboxed frame has an opaque origin, so "*" is the only target; event.source identity is the check.
            if (!cancelled) frameWindow.postMessage({ type: "document", data }, "*", [data]);
          },
          () => {
            if (!cancelled) setStatus("error");
          }
        );
      } else if (message) {
        setStatus(message.type);
        if (message.type === "loaded") {
          setPages(
            message.pageCount !== undefined && message.shownPages !== undefined
              ? { pageCount: message.pageCount, shownPages: message.shownPages }
              : null
          );
        }
      }
    };
    window.addEventListener("message", onMessage);
    return () => {
      cancelled = true;
      window.removeEventListener("message", onMessage);
    };
  }, [pdf]);

  useEffect(() => {
    if (status !== "loaded" || page == null) return;
    frameRef.current?.contentWindow?.postMessage({ type: "page", page }, "*");
  }, [status, page]);

  return (
    <div className="relative flex flex-1 min-h-[40vh] w-full">
      <iframe
        ref={frameRef}
        src={PDF_VIEWER_PATH}
        sandbox="allow-scripts"
        className="flex-1 w-full rounded-md border border-slate-800 bg-slate-200"
        title={title}
      />
      {status !== "loaded" && (
        <div
          role={status === "error" ? "alert" : "status"}
          className={`absolute inset-0 flex items-center justify-center rounded-md p-3 text-xs ${
            status === "error"
              ? "border border-red-500/40 bg-red-950/90 text-red-200"
              : "bg-slate-900/80 text-slate-300"
          }`}
        >
          {status === "error" ? t("errors.preview.document") : t("preview.document.loading")}
        </div>
      )}
      {status === "loaded" && pages && pages.shownPages < pages.pageCount && (
        <div
          role="status"
          className="absolute bottom-2 left-2 rounded bg-slate-900/90 px-2 py-1 text-xs text-slate-200"
        >
          {t("preview.document.pagesTruncated", {
            shown: String(pages.shownPages),
            total: String(pages.pageCount)
          })}
        </div>
      )}
    </div>
  );
}
