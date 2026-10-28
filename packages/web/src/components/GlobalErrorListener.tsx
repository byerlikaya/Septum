"use client";

import { useEffect } from "react";
import { sendFrontendError } from "@/lib/api";

/**
 * Registers global window.onerror and window.onunhandledrejection to report
 * runtime errors and unhandled promise rejections to the backend error log.
 */
function rejectionMessage(reason: unknown): string {
  if (typeof reason === "object" && reason !== null && "message" in reason) return String(reason.message);
  return String(reason);
}

function rejectionStack(reason: unknown): string | undefined {
  return reason instanceof Error ? reason.stack : undefined;
}

export function GlobalErrorListener(): null {
  useEffect(() => {
    const route = typeof window !== "undefined" ? window.location.pathname : "";

    function handleError(
      event: ErrorEvent | PromiseRejectionEvent
    ): void {
      const message =
        event instanceof ErrorEvent
          ? event.message
          : rejectionMessage((event as PromiseRejectionEvent).reason);
      const stack =
        event instanceof ErrorEvent
          ? undefined
          : rejectionStack((event as PromiseRejectionEvent).reason);

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
