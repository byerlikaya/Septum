"use client";

import { useEffect } from "react";
import { sendFrontendError } from "@/lib/api";

/**
 * Registers global window.onerror and window.onunhandledrejection to report
 * runtime errors and unhandled promise rejections to the backend error log.
 */
export function GlobalErrorListener(): null {
  useEffect(() => {
    const route = typeof window !== "undefined" ? window.location.pathname : "";

    function handleError(
      event: ErrorEvent | PromiseRejectionEvent
    ): void {
      const message =
        event instanceof ErrorEvent
          ? event.message
          : String(
              typeof (event as PromiseRejectionEvent).reason === "object" &&
                (event as PromiseRejectionEvent).reason !== null &&
                "message" in (event as PromiseRejectionEvent).reason
                ? (event as PromiseRejectionEvent).reason.message
                : (event as PromiseRejectionEvent).reason
            );
      const stack =
        event instanceof ErrorEvent
          ? undefined
          : (event as PromiseRejectionEvent).reason instanceof Error
            ? (event as PromiseRejectionEvent).reason.stack
            : undefined;

      sendFrontendError({
        message: message.slice(0, 2000),
        stack_trace: stack?.slice(0, 8000),
        route,
        level: "ERROR"
      });
    }

    window.addEventListener("error", handleError);
    window.addEventListener("unhandledrejection", handleError);
    return () => {
      window.removeEventListener("error", handleError);
      window.removeEventListener("unhandledrejection", handleError);
    };
  }, []);

  return null;
}
