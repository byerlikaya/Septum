import "@testing-library/jest-dom";
import { TextEncoder as NodeTextEncoder } from "node:util";

// Guarded because scripts/__tests__/xff-preload.test.cjs runs under @jest-environment node (no DOM).
if (typeof Element !== "undefined") {
  Element.prototype.scrollIntoView = jest.fn();
}

if (typeof globalThis.TextEncoder === "undefined") {
  // jsdom does not expose TextEncoder; the Node implementation matches the browser one.
  globalThis.TextEncoder = NodeTextEncoder as typeof globalThis.TextEncoder;
}
