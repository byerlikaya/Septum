"use client";

import { useEffect, type RefObject } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/**
 * Trap Tab navigation inside the referenced container while ``enabled``.
 *
 * Without this, Tabbing inside an open modal walks past its last
 * focusable element and lands on whatever sits behind the overlay —
 * keyboard users get pushed out of the dialog and the screen reader
 * starts narrating unrelated page chrome. The trap mirrors the
 * standard pattern: query focusable descendants on each Tab, redirect
 * the focus to the opposite end when the user reaches the boundary.
 *
 * ``initialFocusRef`` (or the first focusable descendant when the
 * ref is undefined) receives focus on mount so screen readers and
 * keyboard users land somewhere predictable instead of staying on
 * the trigger button outside the modal.
 */
export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  enabled: boolean,
  initialFocusRef?: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    if (!enabled) return;
    const node = containerRef.current;
    if (!node) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;

    // Push focus into the modal so the trap has somewhere to anchor.
    const initial =
      initialFocusRef?.current ??
      (node.querySelector(FOCUSABLE_SELECTOR) as HTMLElement | null);
    initial?.focus();

    const handleKey = (event: KeyboardEvent): void => {
      if (event.key !== "Tab") return;
      const focusables = node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (focusables.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;

      if (event.shiftKey) {
        if (active === first || !node.contains(active)) {
          event.preventDefault();
          last.focus();
        }
      } else if (active === last || !node.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    };

    node.addEventListener("keydown", handleKey);
    return () => {
      node.removeEventListener("keydown", handleKey);
      // Restore focus to whatever opened the modal so keyboard users
      // continue from where they were.
      previouslyFocused?.focus?.();
    };
  }, [containerRef, enabled, initialFocusRef]);
}
