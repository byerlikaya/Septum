import { baseURL, resolveBaseURL } from "../api";

describe("baseURL (module-load resolution)", () => {
  // jest.setup.ts does not set NEXT_PUBLIC_API_BASE_URL, so the module
  // load resolves to "" and requests fall through Next.js rewrites.
  it("defaults to empty string for same-origin proxy", () => {
    expect(baseURL).toBe("");
  });
});

describe("resolveBaseURL", () => {
  it("returns empty string when env value is undefined", () => {
    expect(resolveBaseURL(undefined)).toBe("");
  });

  it("uses the env value when set", () => {
    expect(resolveBaseURL("https://api.septum.example")).toBe(
      "https://api.septum.example",
    );
  });

  it("strips trailing slashes so callers can concat /api/... cleanly", () => {
    expect(resolveBaseURL("https://api.septum.example/")).toBe(
      "https://api.septum.example",
    );
    expect(resolveBaseURL("https://api.septum.example///")).toBe(
      "https://api.septum.example",
    );
  });
});
