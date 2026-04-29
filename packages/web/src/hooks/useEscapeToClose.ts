"use client";

import { useEffect } from "react";

/**
 * Close a modal when the user presses Escape.
 *
 * Earlier each modal hand-rolled its own ``keydown`` listener (or
 * worse, did not register one at all — so dismissing required a
 * mouse click on a tiny X button). This hook centralises the listener
 * and matches the standard a11y dialog dismiss pattern.
 *
 * Pass ``enabled = open`` so the listener is only attached while the
 * modal is visible — leaving it bound when ``open === false`` would
 * eat ESC presses from any unrelated component below the modal in
 * the tree.
 */
export function useEscapeToClose(
  enabled: boolean,
  onClose: () => void,
): void {
  useEffect(() => {
    if (!enabled) return;
    const handler = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enabled, onClose]);
}
