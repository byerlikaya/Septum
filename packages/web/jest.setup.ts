import "@testing-library/jest-dom";
import { TextEncoder as NodeTextEncoder } from "node:util";

Element.prototype.scrollIntoView = jest.fn();

if (typeof globalThis.TextEncoder === "undefined") {
  // jsdom does not expose TextEncoder; the Node implementation matches the browser one.
  globalThis.TextEncoder = NodeTextEncoder as typeof globalThis.TextEncoder;
}
