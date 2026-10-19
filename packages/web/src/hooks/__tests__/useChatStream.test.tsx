import { act, renderHook, waitFor } from "@testing-library/react";
import type { SSEChatEvent } from "@/lib/types";
import { useChatStream, type UseChatStreamOptions } from "../useChatStream";

jest.mock("../../lib/language", () => ({
  useLanguage: () => ({ language: "en" })
}));

jest.mock("../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

const mockStreamChatAsk = jest.fn();
const mockGetChatDebug = jest.fn();

jest.mock("../../lib/api", () => ({
  streamChatAsk: (...args: unknown[]) => mockStreamChatAsk(...args),
  getChatDebug: (...args: unknown[]) => mockGetChatDebug(...args),
  OLLAMA_URL_NOT_ALLOWED: "ollama_url_not_allowed"
}));

type OnEvent = (event: SSEChatEvent) => void;

function lastOnEvent(): OnEvent {
  const calls = mockStreamChatAsk.mock.calls;
  return calls[calls.length - 1][1] as OnEvent;
}

function renderStream(overrides: Partial<UseChatStreamOptions> = {}) {
  return renderHook(() =>
    useChatStream({
      documentIds: [1],
      requireApproval: true,
      deanonEnabled: true,
      outputMode: "chat",
      onApprovalRequired: jest.fn(),
      onApprovalRejected: jest.fn(),
      ...overrides
    })
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockStreamChatAsk.mockReturnValue({ abort: jest.fn() });
});

it("regenerate after a rejection re-enters approval without pre-approved chunks", async () => {
  const { result } = renderStream();

  act(() => result.current.sendMessage("What is the total?"));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));

  act(() => {
    lastOnEvent()({ type: "approval_rejected", session_id: "s1", timed_out: false });
  });
  await waitFor(() => expect(result.current.streaming).toBe(false));

  act(() => result.current.regenerate());
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(2));

  const [params] = mockStreamChatAsk.mock.calls[1];
  expect(params.message).toBe("What is the total?");
  expect(params.require_approval).toBe(true);
  expect(params.pre_approved_chunks).toBeUndefined();
  expect(result.current.messages.filter((m) => m.role === "user")).toHaveLength(1);
});

it("a retry with chunks still asks for approval when the setting requires it", async () => {
  const { result } = renderStream();

  act(() => result.current.sendMessage("Q", [{ text: "chunk" }]));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));

  const [params] = mockStreamChatAsk.mock.calls[0];
  expect(params.require_approval).toBe(true);
  expect(params.pre_approved_chunks).toEqual([{ text: "chunk" }]);
});

it("fetches debug data by debug_id and never keeps final_answer", async () => {
  mockGetChatDebug.mockResolvedValue({
    debug_id: "dbg-1",
    masked_prompt: "prompt [PERSON_1]",
    masked_answer: "answer [PERSON_1]",
    final_answer: "answer Jane Doe"
  });
  const onMessagePairComplete = jest.fn();
  const { result } = renderStream({ onMessagePairComplete });

  act(() => result.current.sendMessage("Who?"));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));

  act(() => {
    const onEvent = lastOnEvent();
    onEvent({
      type: "meta",
      session_id: "approval-session",
      debug_id: "dbg-1",
      document_id: 1,
      language: "en",
      require_approval: true,
      retrieved_chunk_count: 1,
      active_regulations: []
    });
    onEvent({ type: "answer_chunk", text: "answer Jane Doe" });
    onEvent({ type: "end" });
  });

  await waitFor(() => expect(onMessagePairComplete).toHaveBeenCalledTimes(1));
  expect(mockGetChatDebug).toHaveBeenCalledWith("dbg-1");

  const [, assistantText, debugId, , , debugData] = onMessagePairComplete.mock.calls[0];
  expect(assistantText).toBe("answer Jane Doe");
  expect(debugId).toBe("dbg-1");
  expect(debugData).toEqual({
    masked_prompt: "prompt [PERSON_1]",
    masked_answer: "answer [PERSON_1]"
  });
  expect(debugData).not.toHaveProperty("final_answer");

  const assistant = result.current.messages.find((m) => m.role === "assistant");
  expect(assistant?.debugId).toBe("dbg-1");
  expect(assistant?.debugData).not.toHaveProperty("final_answer");
});

it("opens the debug panel with the rendered answer, not a server final_answer", async () => {
  mockGetChatDebug.mockResolvedValue({
    debug_id: "dbg-2",
    masked_prompt: "p",
    masked_answer: "a",
    final_answer: "server value"
  });
  const { result } = renderStream();

  await act(() => result.current.handleOpenDebug("dbg-2", "rendered answer"));

  expect(mockGetChatDebug).toHaveBeenCalledWith("dbg-2");
  expect(result.current.debugData).toEqual({
    masked_prompt: "p",
    masked_answer: "a",
    final_answer: "rendered answer"
  });
  expect(result.current.debugOpen).toBe(true);
});

it("hands the approval event's numbering token to onApprovalRequired", async () => {
  const onApprovalRequired = jest.fn();
  const { result } = renderStream({ onApprovalRequired });

  act(() => result.current.sendMessage("Q"));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));
  act(() => {
    lastOnEvent()({
      type: "approval_required",
      session_id: "s1",
      numbering_token: "v1.token",
      masked_prompt: "Q",
      assembled_prompt: "P",
      chunks: []
    });
  });

  expect(onApprovalRequired).toHaveBeenCalledWith("s1", "Q", [], [], "P", "v1.token");
});

it("a retry resends the numbering token; a fresh turn never does", async () => {
  const { result } = renderStream();

  act(() => result.current.sendMessage("Q", [{ text: "chunk" }], undefined, { numberingToken: "v1.t" }));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));
  act(() => {
    lastOnEvent()({ type: "end" });
  });
  await waitFor(() => expect(result.current.streaming).toBe(false));
  act(() => result.current.sendMessage("Q2", undefined, undefined, { numberingToken: "v1.t" }));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(2));

  expect(mockStreamChatAsk.mock.calls[0][0].numbering_token).toBe("v1.t");
  expect(mockStreamChatAsk.mock.calls[1][0].numbering_token).toBeUndefined();
});

it("shows the translated message when a retry is refused", async () => {
  const { result } = renderStream();

  act(() => result.current.sendMessage("Q", [{ text: "chunk" }], undefined, { numberingToken: "v1.t" }));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));
  act(() => {
    lastOnEvent()({
      type: "error",
      code: "retry_numbering_changed",
      message: "The documents changed since this answer was prepared; please ask again."
    });
  });

  await waitFor(() => expect(result.current.streamError).toBe("chat.error.retryNumberingChanged"));
  const assistant = result.current.messages.find((m) => m.role === "assistant");
  expect(assistant?.content).toBe("Error: chat.error.retryNumberingChanged");
});

it("shows the translated message when the Ollama URL is refused", async () => {
  const { result } = renderStream();

  act(() => result.current.sendMessage("Q"));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));
  act(() => {
    lastOnEvent()({ type: "error", code: "ollama_url_not_allowed", message: "server text" });
  });

  await waitFor(() => expect(result.current.streamError).toBe("chat.error.ollamaUrlNotAllowed"));
});

it("shows the translated message when a model fails its integrity check", async () => {
  const { result } = renderStream();

  act(() => result.current.sendMessage("Q"));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));
  act(() => {
    lastOnEvent()({ type: "error", code: "model_integrity_failed", message: "server text" });
  });

  await waitFor(() => expect(result.current.streamError).toBe("errors.model.integrityFailed"));
  const assistant = result.current.messages.find((m) => m.role === "assistant");
  expect(assistant?.content).toBe("Error: errors.model.integrityFailed");
});

it("shows the translated message when the custom NER model is not pinned", async () => {
  const { result } = renderStream();

  act(() => result.current.sendMessage("Q"));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));
  act(() => {
    lastOnEvent()({ type: "error", code: "ner_model_unpinned", message: "server text" });
  });

  await waitFor(() => expect(result.current.streamError).toBe("errors.model.nerUnpinned"));
});

it("keeps the server message for an unrecognised error code", async () => {
  const { result } = renderStream();

  act(() => result.current.sendMessage("Q"));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));
  act(() => {
    lastOnEvent()({ type: "error", code: "something_else", message: "server text" });
  });

  await waitFor(() => expect(result.current.streamError).toBe("server text"));
});

it("restores initialMessages on first render", () => {
  const initial = [
    { id: "a", role: "user" as const, content: "hi" },
    { id: "b", role: "assistant" as const, content: "hello" }
  ];
  const { result } = renderStream({ initialMessages: initial });
  expect(result.current.messages).toEqual(initial);
  expect(result.current.pendingAssistantId).toBeNull();
});

it("tracks pendingAssistantId while streaming and clears it on end", async () => {
  const { result } = renderStream({ requireApproval: false });

  act(() => result.current.sendMessage("Question?"));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));

  const pending = result.current.pendingAssistantId;
  expect(pending).not.toBeNull();
  expect(result.current.messages[result.current.messages.length - 1].id).toBe(pending);

  act(() => {
    lastOnEvent()({ type: "answer_chunk", text: "partial" } as SSEChatEvent);
  });
  expect(result.current.messages.find((m) => m.id === pending)?.content).toBe("partial");

  act(() => {
    lastOnEvent()({ type: "end" } as SSEChatEvent);
  });
  expect(result.current.pendingAssistantId).toBeNull();
  expect(result.current.streaming).toBe(false);
});

it("clears pendingAssistantId when streaming is stopped", async () => {
  const { result } = renderStream();
  act(() => result.current.sendMessage("Question?"));
  await waitFor(() => expect(mockStreamChatAsk).toHaveBeenCalledTimes(1));
  expect(result.current.pendingAssistantId).not.toBeNull();
  act(() => result.current.stopStreaming());
  expect(result.current.pendingAssistantId).toBeNull();
});
