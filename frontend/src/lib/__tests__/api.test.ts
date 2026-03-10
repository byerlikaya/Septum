import { baseURL } from "../api";

describe("api", () => {
  it("exports baseURL", () => {
    expect(typeof baseURL).toBe("string");
    expect(baseURL.length).toBeGreaterThan(0);
  });

  it("uses fallback or env for baseURL", () => {
    expect(baseURL).toMatch(/^https?:\/\//);
  });
});
