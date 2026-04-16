import { baseURL } from "../api";

describe("api baseURL", () => {
  // jest.setup.ts does not set NEXT_PUBLIC_API_BASE_URL, so the default
  // resolution path ("" → same-origin proxy through Next.js rewrites)
  // is exercised here. The override path is covered in the isolated
  // describe block below using jest.isolateModules.

  it("defaults to empty string for same-origin proxy", () => {
    expect(typeof baseURL).toBe("string");
    expect(baseURL).toBe("");
  });
});

describe("api baseURL with NEXT_PUBLIC_API_BASE_URL override", () => {
  const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;

  afterEach(() => {
    if (ORIGINAL_ENV === undefined) {
      delete process.env.NEXT_PUBLIC_API_BASE_URL;
    } else {
      process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    }
  });

  function loadBaseURL(envValue: string): string {
    process.env.NEXT_PUBLIC_API_BASE_URL = envValue;
    let resolved = "";
    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      resolved = require("../api").baseURL;
    });
    return resolved;
  }

  it("uses the env value when set", () => {
    expect(loadBaseURL("https://api.septum.example")).toBe(
      "https://api.septum.example",
    );
  });

  it("strips trailing slashes so callers can concat /api/... cleanly", () => {
    expect(loadBaseURL("https://api.septum.example/")).toBe(
      "https://api.septum.example",
    );
    expect(loadBaseURL("https://api.septum.example///")).toBe(
      "https://api.septum.example",
    );
  });
});
