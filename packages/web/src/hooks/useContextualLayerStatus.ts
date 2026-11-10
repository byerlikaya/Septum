"use client";

import { useEffect, useState } from "react";
import { getContextualLayerStatus } from "@/lib/api";
import type { ContextualLayerStatus } from "@/lib/types";

export interface ContextualLayerStatusResult {
  status: ContextualLayerStatus | null;
  error: boolean;
}

/**
 * Fetches the contextual layer coverage once on mount. While loading both
 * fields are empty so callers stay silent; a failed call, a malformed body or
 * an unknown state sets `error` so callers can say coverage is unverified.
 */
export function useContextualLayerStatus(): ContextualLayerStatusResult {
  const [result, setResult] = useState<ContextualLayerStatusResult>({
    status: null,
    error: false,
  });

  useEffect(() => {
    let cancelled = false;
    getContextualLayerStatus()
      .then((status) => {
        if (!cancelled) setResult({ status, error: false });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        console.error("Could not load contextual layer status", err);
        setResult({ status: null, error: true });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return result;
}
