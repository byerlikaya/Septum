import { AxiosError, type InternalAxiosRequestConfig } from "axios";
import api, {
  clearSetupToken,
  getSetupToken,
  onSetupForbidden,
  setSetupToken,
  streamSSE,
  verifySetupToken,
} from "../api";

const originalAdapter = api.defaults.adapter;

function setupForbiddenError(config: InternalAxiosRequestConfig) {
  return new AxiosError("Forbidden", "ERR_BAD_REQUEST", config, null, {
    status: 403,
    statusText: "Forbidden",
    headers: {},
    config,
    data: { detail: "Setup access denied.", code: "setup_forbidden" },
  });
}

let seenHeaders: Record<string, string>[] = [];
let respondWith: ((config: InternalAxiosRequestConfig) => unknown) | null = null;

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  seenHeaders = [];
  respondWith = null;
  api.defaults.adapter = async (config) => {
    seenHeaders.push({ ...config.headers.toJSON() } as Record<string, string>);
    if (respondWith) return respondWith(config) as never;
    return { data: {}, status: 200, statusText: "OK", headers: {}, config };
  };
});

afterEach(() => {
  api.defaults.adapter = originalAdapter;
});

const headerValue = (headers: Record<string, string>, name: string) =>
  Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1];

describe("setup token storage", () => {
  it("keeps the token in sessionStorage only", () => {
    setSetupToken("tok-1");

    expect(sessionStorage.getItem("septum_setup_token")).toBe("tok-1");
    expect(localStorage.length).toBe(0);
    expect(getSetupToken()).toBe("tok-1");

    clearSetupToken();
    expect(getSetupToken()).toBeNull();
  });
});

describe("X-Setup-Token header", () => {
  it("attaches the stored token to every request", async () => {
    setSetupToken("tok-1");

    await api.get("/api/setup/status");

    expect(headerValue(seenHeaders[0], "X-Setup-Token")).toBe("tok-1");
  });

  it("is absent when no token is stored", async () => {
    await api.get("/api/setup/status");

    expect(headerValue(seenHeaders[0], "X-Setup-Token")).toBeUndefined();
  });

  it("sends the candidate token on verify, even when another token is stored", async () => {
    setSetupToken("stored");

    await verifySetupToken("candidate");

    expect(headerValue(seenHeaders[0], "X-Setup-Token")).toBe("candidate");
  });

  it("is attached to the SSE helper when a token is stored", async () => {
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, body: null });
    const originalFetch = global.fetch;
    global.fetch = fetchMock as unknown as typeof fetch;
    try {
      setSetupToken("tok-sse");

      streamSSE("/api/setup/install-whisper", { model: "base" }, () => {});
      await new Promise((resolve) => setTimeout(resolve, 0));

      const init = fetchMock.mock.calls[0][1] as RequestInit;
      expect((init.headers as Record<string, string>)["X-Setup-Token"]).toBe("tok-sse");
    } finally {
      global.fetch = originalFetch;
    }
  });
});

describe("setup-gate 403 handling", () => {
  it("clears the stored token and notifies listeners on setup_forbidden", async () => {
    setSetupToken("tok-1");
    respondWith = (config) => { throw setupForbiddenError(config); };
    const listener = jest.fn();
    const unsubscribe = onSetupForbidden(listener);

    await expect(api.get("/api/setup/status")).rejects.toThrow();

    expect(getSetupToken()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("does not notify listeners after unsubscribing", async () => {
    setSetupToken("tok-1");
    respondWith = (config) => { throw setupForbiddenError(config); };
    const listener = jest.fn();
    const unsubscribe = onSetupForbidden(listener);
    unsubscribe();

    await expect(api.get("/api/setup/status")).rejects.toThrow();

    expect(listener).not.toHaveBeenCalled();
  });

  it("leaves the token and listeners alone when verify-token answers 403", async () => {
    setSetupToken("stored");
    respondWith = (config) => { throw setupForbiddenError(config); };
    const listener = jest.fn();
    const unsubscribe = onSetupForbidden(listener);

    await expect(verifySetupToken("wrong")).rejects.toThrow();

    expect(listener).not.toHaveBeenCalled();
    expect(getSetupToken()).toBe("stored");
    unsubscribe();
  });

  it("ignores a 403 that does not carry the setup_forbidden code", async () => {
    setSetupToken("tok-1");
    respondWith = (config) => {
      throw new AxiosError("Forbidden", "ERR_BAD_REQUEST", config, null, {
        status: 403,
        statusText: "Forbidden",
        headers: {},
        config,
        data: { detail: "Other" },
      });
    };
    const listener = jest.fn();
    const unsubscribe = onSetupForbidden(listener);

    await expect(api.get("/api/chat-sessions")).rejects.toThrow();

    expect(listener).not.toHaveBeenCalled();
    expect(getSetupToken()).toBe("tok-1");
    unsubscribe();
  });

  it("clears the token and notifies listeners when the SSE helper gets setup_forbidden", async () => {
    setSetupToken("tok-sse");
    const fetchMock = jest.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ code: "setup_forbidden" }),
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock as unknown as typeof fetch;
    const listener = jest.fn();
    const unsubscribe = onSetupForbidden(listener);
    const onHttpError = jest.fn();
    try {
      streamSSE("/api/setup/install-whisper", { model: "base" }, () => {}, { onHttpError });
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(getSetupToken()).toBeNull();
      expect(listener).toHaveBeenCalledTimes(1);
      expect(onHttpError).toHaveBeenCalledWith("setup_forbidden");
    } finally {
      global.fetch = originalFetch;
      unsubscribe();
    }
  });
});
