import { TextDecoder as NodeTextDecoder, TextEncoder as NodeTextEncoder } from "node:util";
import {
  MAX_PENDING_LINE_CHARS,
  clearAuthToken,
  setAuthToken,
  setSetupToken,
  streamChatAsk,
  streamSSE,
} from "../api";
import type { SSEChatEvent } from "../types";

if (typeof globalThis.TextDecoder === "undefined") {
  globalThis.TextDecoder = NodeTextDecoder as typeof globalThis.TextDecoder;
}
if (typeof globalThis.TextEncoder === "undefined") {
  globalThis.TextEncoder = NodeTextEncoder as typeof globalThis.TextEncoder;
}

const originalFetch = global.fetch;
const encoder = new NodeTextEncoder();
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function streamBody(chunks: string[]) {
  const queue = chunks.map((chunk) => encoder.encode(chunk));
  return {
    getReader: () => ({
      read: async () =>
        queue.length > 0
          ? { done: false, value: queue.shift() }
          : { done: true, value: undefined },
    }),
  };
}

type FetchSpy = jest.Mock<Promise<unknown>, [string, RequestInit?]>;

function stubFetch(response: unknown): FetchSpy {
  const spy = jest.fn<Promise<unknown>, [string, RequestInit?]>(async () => response);
  global.fetch = spy as unknown as typeof fetch;
  return spy;
}

function requestInit(spy: FetchSpy): RequestInit {
  return spy.mock.calls[0][1] as RequestInit;
}

function requestHeaders(spy: FetchSpy): Record<string, string> {
  return requestInit(spy).headers as Record<string, string>;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  global.fetch = originalFetch;
});

describe("streamSSE request shaping", () => {
  it("sends a GET with no body and no Content-Type when no body is given", async () => {
    const spy = stubFetch({ ok: true, body: null });

    streamSSE("/api/status", undefined, () => {});
    await flush();

    expect(spy.mock.calls[0][0]).toBe("/api/status");
    expect(requestInit(spy).method).toBeUndefined();
    expect(requestInit(spy)).not.toHaveProperty("body");
    expect(requestHeaders(spy)).not.toHaveProperty("Content-Type");
  });

  it("switches to a JSON POST when a body is given", async () => {
    const spy = stubFetch({ ok: true, body: null });

    streamSSE("/api/install", { model: "base" }, () => {});
    await flush();

    expect(requestInit(spy).method).toBe("POST");
    expect(requestInit(spy).body).toBe('{"model":"base"}');
    expect(requestHeaders(spy)["Content-Type"]).toBe("application/json");
  });

  it("sends an empty JSON object for an explicit POST without a body", async () => {
    const spy = stubFetch({ ok: true, body: null });

    streamSSE("/api/pull", undefined, () => {}, { method: "POST" });
    await flush();

    expect(requestInit(spy).method).toBe("POST");
    expect(requestInit(spy).body).toBe("{}");
  });

  it("sends a GET with a body as a POST, since a body always means POST", async () => {
    const spy = stubFetch({ ok: true, body: null });

    streamSSE("/api/x", { a: 1 }, () => {}, { method: "GET" });
    await flush();

    expect(requestInit(spy).method).toBe("POST");
  });

  it("attaches the bearer token and the setup token when both are stored", async () => {
    setAuthToken("auth-1");
    setSetupToken("setup-1");
    const spy = stubFetch({ ok: true, body: null });

    streamSSE("/api/x", undefined, () => {});
    await flush();

    expect(requestHeaders(spy).Authorization).toBe("Bearer auth-1");
    expect(requestHeaders(spy)["X-Setup-Token"]).toBe("setup-1");
  });

  it("omits Authorization when no auth token is stored", async () => {
    const spy = stubFetch({ ok: true, body: null });

    streamSSE("/api/x", undefined, () => {});
    await flush();

    expect(requestHeaders(spy)).not.toHaveProperty("Authorization");
  });

  it("aborts the request signal through the returned handle", async () => {
    const spy = stubFetch({ ok: true, body: null });

    const handle = streamSSE("/api/x", undefined, () => {});
    await flush();
    handle.abort();

    expect(requestInit(spy).signal?.aborted).toBe(true);
  });
});

describe("streamSSE line handling", () => {
  it("emits data payloads split across chunks and ignores other lines", async () => {
    stubFetch({
      ok: true,
      body: streamBody(['data: {"a"', ':1}\n: keepalive\nevent: x\ndata: second\n', "data: third"]),
    });
    const onLine = jest.fn();

    streamSSE("/api/x", undefined, onLine);
    await flush();

    expect(onLine.mock.calls.map((c) => c[0])).toEqual(['{"a":1}', "second"]);
  });
});

describe("streamSSE failures", () => {
  it("reports a null code when the error body is not JSON", async () => {
    stubFetch({ ok: false, status: 500, json: () => Promise.reject(new Error("not json")) });
    const onLine = jest.fn();
    const onHttpError = jest.fn();

    streamSSE("/api/x", undefined, onLine, { onHttpError });
    await flush();

    expect(onHttpError).toHaveBeenCalledWith(null);
    expect(onLine).not.toHaveBeenCalled();
  });

  it("reports the server code on a non-setup failure and keeps the setup token", async () => {
    setSetupToken("tok-keep");
    stubFetch({ ok: false, status: 400, json: async () => ({ code: "ollama_url_not_allowed" }) });
    const onHttpError = jest.fn();

    streamSSE("/api/x", undefined, () => {}, { onHttpError });
    await flush();

    expect(onHttpError).toHaveBeenCalledWith("ollama_url_not_allowed");
    expect(sessionStorage.getItem("septum_setup_token")).toBe("tok-keep");
  });

  it("neither emits nor reports when the successful response has no body", async () => {
    stubFetch({ ok: true, body: null });
    const onLine = jest.fn();
    const onHttpError = jest.fn();

    streamSSE("/api/x", undefined, onLine, { onHttpError });
    await flush();

    expect(onLine).not.toHaveBeenCalled();
    expect(onHttpError).not.toHaveBeenCalled();
  });

  it("swallows a rejected fetch without reporting", async () => {
    global.fetch = jest.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    const onLine = jest.fn();
    const onHttpError = jest.fn();

    streamSSE("/api/x", undefined, onLine, { onHttpError });
    await flush();

    expect(onLine).not.toHaveBeenCalled();
    expect(onHttpError).not.toHaveBeenCalled();
  });
});

describe("streamChatAsk request", () => {
  const ask = (params: Parameters<typeof streamChatAsk>[0]) => {
    const onEvent = jest.fn();
    streamChatAsk(params, onEvent);
    return onEvent;
  };
  const sentBody = (spy: FetchSpy) => JSON.parse(requestInit(spy).body as string);

  it("posts to the ask endpoint with the bearer token and no setup token", async () => {
    setAuthToken("auth-chat");
    setSetupToken("setup-chat");
    const spy = stubFetch({ ok: false, status: 500, statusText: "x", text: async () => "" });

    ask({ message: "hi" });
    await flush();

    expect(spy.mock.calls[0][0]).toBe("/api/chat/ask");
    expect(requestInit(spy).method).toBe("POST");
    expect(requestHeaders(spy)).toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer auth-chat",
    });
  });

  it("omits Authorization when no auth token is stored", async () => {
    clearAuthToken();
    const spy = stubFetch({ ok: false, status: 500, statusText: "x", text: async () => "" });

    ask({ message: "hi" });
    await flush();

    expect(requestHeaders(spy)).toEqual({ "Content-Type": "application/json" });
  });

  it("sends defaults and drops unset optional fields from the body", async () => {
    const spy = stubFetch({ ok: false, status: 500, statusText: "x", text: async () => "" });

    ask({ message: "hi" });
    await flush();

    expect(sentBody(spy)).toEqual({ message: "hi", output_mode: "chat" });
  });

  it("uses the requested output mode and forwards the optional fields", async () => {
    const spy = stubFetch({ ok: false, status: 500, statusText: "x", text: async () => "" });

    ask({
      message: "hi",
      top_k: 4,
      session_id: "s1",
      output_mode: "json",
      require_approval: true,
      deanon_enabled: false,
      pre_approved_chunks: [{ text: "c", document_id: 2 }],
      numbering_token: "n1",
    });
    await flush();

    expect(sentBody(spy)).toEqual({
      message: "hi",
      top_k: 4,
      session_id: "s1",
      output_mode: "json",
      require_approval: true,
      deanon_enabled: false,
      pre_approved_chunks: [{ text: "c", document_id: 2 }],
      numbering_token: "n1",
    });
  });

  it("falls back to the single document id when no list is given", async () => {
    const spy = stubFetch({ ok: false, status: 500, statusText: "x", text: async () => "" });

    ask({ message: "hi", document_id: 3 });
    await flush();

    expect(sentBody(spy)).toMatchObject({ document_id: 3, document_ids: [3] });
  });

  it("keeps an explicit document id list alongside the single id", async () => {
    const spy = stubFetch({ ok: false, status: 500, statusText: "x", text: async () => "" });

    ask({ message: "hi", document_id: 3, document_ids: [3, 4] });
    await flush();

    expect(sentBody(spy)).toMatchObject({ document_id: 3, document_ids: [3, 4] });
  });

  it("omits scoped document ids when the list is empty", async () => {
    const spy = stubFetch({ ok: false, status: 500, statusText: "x", text: async () => "" });

    ask({ message: "hi", scoped_doc_ids: [] });
    await flush();

    expect(sentBody(spy)).not.toHaveProperty("scoped_doc_ids");
  });

  it("sends scoped document ids when the list is non-empty", async () => {
    const spy = stubFetch({ ok: false, status: 500, statusText: "x", text: async () => "" });

    ask({ message: "hi", scoped_doc_ids: [5] });
    await flush();

    expect(sentBody(spy)).toMatchObject({ scoped_doc_ids: [5] });
  });
});

describe("streamChatAsk events", () => {
  const run = async (chunks: string[]) => {
    global.fetch = jest.fn(async () => ({ ok: true, status: 200, body: streamBody(chunks) })) as unknown as typeof fetch;
    const onEvent = jest.fn();
    streamChatAsk({ message: "hi" }, onEvent);
    await flush();
    return onEvent.mock.calls.map((c) => c[0] as SSEChatEvent);
  };

  it("delivers parsed events and skips [DONE], blank and malformed payloads", async () => {
    const events = await run([
      'data: {"type":"token","text":"a"}\n\ndata: [DONE]\ndata:   \ndata: {bad\n',
      'data: {"type":"end"}\n',
    ]);

    expect(events).toEqual([{ type: "token", text: "a" }, { type: "end" }]);
  });

  it("stops delivering after an end event", async () => {
    const events = await run(['data: {"type":"end"}\ndata: {"type":"token","text":"late"}\n']);

    expect(events).toEqual([{ type: "end" }]);
  });

  it("stops delivering after an error event", async () => {
    const events = await run([
      'data: {"type":"error","message":"boom"}\ndata: {"type":"token","text":"late"}\n',
    ]);

    expect(events).toEqual([{ type: "error", message: "boom" }]);
  });

  it("delivers a final data line that arrives without a trailing newline", async () => {
    const events = await run(['data: {"type":"token","text":"tail"}']);

    expect(events).toEqual([{ type: "token", text: "tail" }]);
  });

  it("emits nothing and no error for a trailing [DONE] without a final newline", async () => {
    const events = await run(["data: [DONE]"]);

    expect(events).toEqual([]);
  });

  it("ignores a trailing line that is not a data line", async () => {
    const events = await run(["event: ping"]);

    expect(events).toEqual([]);
  });
});

describe("streamChatAsk failures", () => {
  const failWith = async (response: unknown) => {
    global.fetch = jest.fn(async () => response) as unknown as typeof fetch;
    const onEvent = jest.fn();
    streamChatAsk({ message: "hi" }, onEvent);
    await flush();
    return onEvent.mock.calls.map((c) => c[0]);
  };

  it("reports the HTTP status, status text and response detail", async () => {
    const events = await failWith({
      ok: false,
      status: 422,
      statusText: "Unprocessable",
      text: async () => "bad input",
    });

    expect(events).toEqual([{ type: "error", message: "HTTP 422: Unprocessable — bad input" }]);
  });

  it("truncates a long error detail to 200 characters with an ellipsis", async () => {
    const events = await failWith({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      text: async () => "x".repeat(300),
    });

    expect(events).toEqual([
      { type: "error", message: `HTTP 500: Internal Server Error — ${"x".repeat(200)}…` },
    ]);
  });

  it("omits the separator when the error body is empty", async () => {
    const events = await failWith({
      ok: false,
      status: 404,
      statusText: "Not Found",
      text: async () => "",
    });

    expect(events).toEqual([{ type: "error", message: "HTTP 404: Not Found" }]);
  });

  it("reports a missing response body", async () => {
    const events = await failWith({ ok: true, status: 200, body: null });

    expect(events).toEqual([{ type: "error", message: "No response body" }]);
  });

  it("reports the thrown message when the request fails", async () => {
    global.fetch = jest.fn(async () => {
      throw new Error("network down");
    }) as unknown as typeof fetch;
    const onEvent = jest.fn();
    streamChatAsk({ message: "hi" }, onEvent);
    await flush();

    expect(onEvent).toHaveBeenCalledWith({ type: "error", message: "network down" });
  });

  it("reports a generic message when the thrown value is not an Error", async () => {
    global.fetch = jest.fn(async () => {
      throw "boom";
    }) as unknown as typeof fetch;
    const onEvent = jest.fn();
    streamChatAsk({ message: "hi" }, onEvent);
    await flush();

    expect(onEvent).toHaveBeenCalledWith({ type: "error", message: "Stream failed" });
  });

  it("stays silent on an abort", async () => {
    global.fetch = jest.fn(async () => {
      throw Object.assign(new Error("aborted"), { name: "AbortError" });
    }) as unknown as typeof fetch;
    const onEvent = jest.fn();
    streamChatAsk({ message: "hi" }, onEvent);
    await flush();

    expect(onEvent).not.toHaveBeenCalled();
  });

  it("reports a read failure mid-stream", async () => {
    const body = {
      getReader: () => ({
        read: async () => {
          throw new Error("connection reset");
        },
      }),
    };

    const events = await failWith({ ok: true, status: 200, body });

    expect(events).toEqual([{ type: "error", message: "connection reset" }]);
  });
});

function trackedStreamBody(chunks: string[], cancelRejects = false) {
  const queue = chunks.map((chunk) => encoder.encode(chunk));
  const cancel = jest.fn(async () => {
    if (cancelRejects) throw new Error("cancel failed");
  });
  const body = {
    getReader: () => ({
      read: async () =>
        queue.length > 0
          ? { done: false, value: queue.shift() }
          : { done: true, value: undefined },
      cancel,
    }),
  };
  return { body, cancel, unread: () => queue.length };
}

const LINE_TOO_LONG_CODE = "stream_line_too_long";

describe("streamChatAsk pending line cap", () => {
  const oversizedLine = `data: ${"x".repeat(MAX_PENDING_LINE_CHARS)}`;

  it("cancels the reader, reports the overflow and emits nothing after it", async () => {
    const { body, cancel, unread } = trackedStreamBody([
      'data: {"type":"token","text":"a"}\n',
      oversizedLine,
      '\ndata: {"type":"token","text":"late"}\n',
    ]);
    global.fetch = jest.fn(async () => ({ ok: true, status: 200, body })) as unknown as typeof fetch;
    const onEvent = jest.fn();

    streamChatAsk({ message: "hi" }, onEvent);
    await flush();

    expect(cancel).toHaveBeenCalledTimes(1);
    expect(unread()).toBe(1);
    expect(onEvent.mock.calls.map((c) => c[0])).toEqual([
      { type: "token", text: "a" },
      { type: "error", message: expect.any(String), code: LINE_TOO_LONG_CODE },
    ]);
  });

  it("reports the overflow with its code even when cancelling the reader rejects", async () => {
    const { body, cancel } = trackedStreamBody(
      ['data: {"type":"token","text":"a"}\n', oversizedLine],
      true,
    );
    global.fetch = jest.fn(async () => ({ ok: true, status: 200, body })) as unknown as typeof fetch;
    const onEvent = jest.fn();

    streamChatAsk({ message: "hi" }, onEvent);
    await flush();

    expect(cancel).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls.map((c) => c[0])).toEqual([
      { type: "token", text: "a" },
      { type: "error", message: expect.any(String), code: LINE_TOO_LONG_CODE },
    ]);
  });

  it("still delivers a line just under the cap when it arrives split across chunks", async () => {
    const prefix = 'data: {"type":"token","text":"';
    const suffix = '"}';
    const fill = "x".repeat(MAX_PENDING_LINE_CHARS - 1 - prefix.length - suffix.length);
    const { body, cancel } = trackedStreamBody([`${prefix}${fill}`, `${suffix}\n`]);
    global.fetch = jest.fn(async () => ({ ok: true, status: 200, body })) as unknown as typeof fetch;
    const onEvent = jest.fn();

    streamChatAsk({ message: "hi" }, onEvent);
    await flush();

    expect(cancel).not.toHaveBeenCalled();
    expect(onEvent.mock.calls.map((c) => c[0])).toEqual([{ type: "token", text: fill }]);
  });
});

describe("streamSSE pending line cap", () => {
  it("cancels the reader and emits nothing after an unterminated overflowing line", async () => {
    const { body, cancel, unread } = trackedStreamBody([
      "data: first\n",
      `data: ${"x".repeat(MAX_PENDING_LINE_CHARS)}`,
      "\ndata: late\n",
    ]);
    stubFetch({ ok: true, body });
    const onLine = jest.fn();
    const onHttpError = jest.fn();

    streamSSE("/api/x", undefined, onLine, { onHttpError });
    await flush();

    expect(cancel).toHaveBeenCalledTimes(1);
    expect(unread()).toBe(1);
    expect(onLine.mock.calls.map((c) => c[0])).toEqual(["first"]);
    expect(onHttpError).not.toHaveBeenCalled();
  });

  it("reports the overflow code through onStreamError", async () => {
    const { body } = trackedStreamBody([`data: ${"x".repeat(MAX_PENDING_LINE_CHARS)}`]);
    stubFetch({ ok: true, body });
    const onStreamError = jest.fn();

    streamSSE("/api/x", undefined, () => {}, { onStreamError });
    await flush();

    expect(onStreamError).toHaveBeenCalledWith(LINE_TOO_LONG_CODE);
  });

  it("reports the overflow through onStreamError even when cancelling rejects", async () => {
    const { body } = trackedStreamBody([`data: ${"x".repeat(MAX_PENDING_LINE_CHARS)}`], true);
    stubFetch({ ok: true, body });
    const onStreamError = jest.fn();

    streamSSE("/api/x", undefined, () => {}, { onStreamError });
    await flush();

    expect(onStreamError).toHaveBeenCalledWith(LINE_TOO_LONG_CODE);
  });

  it("does not call onStreamError for a transport failure or a normal stream", async () => {
    global.fetch = jest.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    const onStreamError = jest.fn();
    streamSSE("/api/x", undefined, () => {}, { onStreamError });
    await flush();

    stubFetch({ ok: true, body: streamBody(['data: {"done":true}\n']) });
    streamSSE("/api/x", undefined, () => {}, { onStreamError });
    await flush();

    expect(onStreamError).not.toHaveBeenCalled();
  });

  it("still delivers a line just under the cap", async () => {
    const payload = "x".repeat(MAX_PENDING_LINE_CHARS - 1 - "data: ".length);
    const { body, cancel } = trackedStreamBody([`data: ${payload.slice(0, 1000)}`, `${payload.slice(1000)}\n`]);
    stubFetch({ ok: true, body });
    const onLine = jest.fn();

    streamSSE("/api/x", undefined, onLine);
    await flush();

    expect(cancel).not.toHaveBeenCalled();
    expect(onLine).toHaveBeenCalledTimes(1);
    expect(onLine.mock.calls[0][0]).toBe(payload);
  });
});

describe("streamSSE end reporting", () => {
  it("calls onStreamEnd once when the reader finishes normally", async () => {
    stubFetch({ ok: true, body: streamBody(['data: {"done":true}\n']) });
    const onStreamEnd = jest.fn();

    streamSSE("/api/x", undefined, () => {}, { onStreamEnd });
    await flush();

    expect(onStreamEnd).toHaveBeenCalledTimes(1);
  });

  it("calls onStreamEnd for a successful response that has no body", async () => {
    stubFetch({ ok: true, body: null });
    const onStreamEnd = jest.fn();

    streamSSE("/api/x", undefined, () => {}, { onStreamEnd });
    await flush();

    expect(onStreamEnd).toHaveBeenCalledTimes(1);
  });

  it("does not call onStreamEnd when the read is aborted", async () => {
    const body = {
      getReader: () => ({
        read: async () => {
          throw Object.assign(new Error("aborted"), { name: "AbortError" });
        },
      }),
    };
    stubFetch({ ok: true, body });
    const onStreamEnd = jest.fn();

    streamSSE("/api/x", undefined, () => {}, { onStreamEnd });
    await flush();

    expect(onStreamEnd).not.toHaveBeenCalled();
  });

  it("does not call onStreamEnd when the stream is cut for being too large", async () => {
    const { body } = trackedStreamBody([`data: ${"x".repeat(MAX_PENDING_LINE_CHARS)}`]);
    stubFetch({ ok: true, body });
    const onStreamEnd = jest.fn();

    streamSSE("/api/x", undefined, () => {}, { onStreamEnd });
    await flush();

    expect(onStreamEnd).not.toHaveBeenCalled();
  });
});

describe("streamChatAsk line delivery across many chunks", () => {
  it("delivers one line split across many small chunks whole", async () => {
    const line = `data: {"type":"token","text":"${"x".repeat(4000)}"}\n`;
    const chunks: string[] = [];
    for (let i = 0; i < line.length; i += 7) chunks.push(line.slice(i, i + 7));
    global.fetch = jest.fn(async () => ({ ok: true, status: 200, body: streamBody(chunks) })) as unknown as typeof fetch;
    const onEvent = jest.fn();

    streamChatAsk({ message: "hi" }, onEvent);
    await flush();

    expect(onEvent.mock.calls.map((c) => c[0])).toEqual([{ type: "token", text: "x".repeat(4000) }]);
  });
});
