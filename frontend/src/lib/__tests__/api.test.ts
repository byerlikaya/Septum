import { baseURL } from "../api";

describe("api", () => {
  it("exports baseURL as empty string for same-origin proxy", () => {
    // In the browser (jsdom), baseURL is "" so requests use relative URLs
    // and Next.js rewrites proxy them to the backend.
    expect(typeof baseURL).toBe("string");
    expect(baseURL).toBe("");
  });
});
