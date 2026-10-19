import { hasMessage } from "../i18n";

describe("hasMessage", () => {
  it("is true for a real catalogue key", () => {
    expect(hasMessage("regulations.desc.gdpr")).toBe(true);
  });

  it("is false for inherited Object.prototype properties", () => {
    expect(hasMessage("constructor")).toBe(false);
    expect(hasMessage("toString")).toBe(false);
  });
});
