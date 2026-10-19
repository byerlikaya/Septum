import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatWindow } from "../ChatWindow";

jest.mock("../../../lib/language", () => ({
  useLanguage: () => ({ language: "en" })
}));

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

const mockStreamChatAsk = jest.fn();
const mockApprovalApprove = jest.fn();
const mockApprovalReject = jest.fn();
const mockGetChatDebug = jest.fn();

jest.mock("../../../lib/api", () => {
  const original = jest.requireActual("../../../lib/api");
  return {
    ...original,
    streamChatAsk: (...args: unknown[]) => mockStreamChatAsk(...args),
    approvalApprove: (...args: unknown[]) => mockApprovalApprove(...args),
    approvalReject: (...args: unknown[]) => mockApprovalReject(...args),
    getChatDebug: (...args: unknown[]) => mockGetChatDebug(...args)
  };
});

beforeEach(() => {
  jest.clearAllMocks();
});

function renderChatWindow(documentIds: number[] = [1]) {
  return render(
    <ChatWindow
      documentIds={documentIds}
      requireApproval={false}
      deanonEnabled={true}
      showJsonOutput={true}
    />
  );
}

it("sends a message and calls streamChatAsk with expected params", async () => {
  mockStreamChatAsk.mockReturnValue({ abort: jest.fn() });

  renderChatWindow();

  const input = screen.getByPlaceholderText("chat.input.placeholder");
  fireEvent.change(input, { target: { value: "Hello" } });

  const sendButton = screen.getByRole("button", { name: "chat.button.send" });
  fireEvent.click(sendButton);

  await waitFor(() => {
    expect(mockStreamChatAsk).toHaveBeenCalledTimes(1);
  });
  const [params] = mockStreamChatAsk.mock.calls[0];
  expect(params.message).toBe("Hello");
  expect(params.document_id).toBe(1);
  expect(params.document_ids).toEqual([1]);
});

it("renders empty state when there are no messages", () => {
  renderChatWindow();
  expect(screen.getByText("chat.emptyState")).toBeInTheDocument();
});


it("a retry from a rejected approval resends each chunk's source document id", async () => {
  mockStreamChatAsk.mockReturnValue({ abort: jest.fn() });
  const chunk = (id: number, documentId: number | null, text: string) => ({
    id,
    document_id: documentId,
    text,
    source_page: null,
    source_slide: null,
    source_sheet: null,
    source_timestamp_start: null,
    source_timestamp_end: null,
    section_title: null,
  });

  render(
    <ChatWindow
      documentIds={[]}
      requireApproval={true}
      deanonEnabled={true}
      showJsonOutput={false}
      initialMessages={[
        {
          role: "user",
          content: "Who owns what?",
          approvalData: {
            decision: "rejected",
            masked_prompt: "Who owns what?",
            chunks: [
              chunk(1, 7, "[PERSON_1] owns the house."),
              chunk(2, 9, "[PERSON_2] owns the car."),
              chunk(3, null, "added note"),
            ],
            regulations: ["GDPR"],
            original_user_message: "Who owns what?",
            numbering_token: "v1.signed",
          },
        },
      ]}
    />
  );

  fireEvent.click(screen.getByRole("button", { name: "chat.approval.badge.rejected" }));
  fireEvent.click(screen.getByRole("button", { name: "chat.approval.button.retry" }));

  await waitFor(() => {
    expect(mockStreamChatAsk).toHaveBeenCalledTimes(1);
  });
  const [params] = mockStreamChatAsk.mock.calls[0];
  expect(params.document_ids).toBeUndefined();
  expect(params.pre_approved_chunks).toEqual([
    { text: "[PERSON_1] owns the house.", document_id: 7 },
    { text: "[PERSON_2] owns the car.", document_id: 9 },
    { text: "added note", document_id: null },
  ]);
  expect(params.numbering_token).toBe("v1.signed");
});

it("announces a stream error in an alert region", async () => {
  mockStreamChatAsk.mockImplementation((_params: unknown, onEvent: (e: unknown) => void) => {
    onEvent({ type: "error", message: "LLM is down" });
    return { abort: jest.fn() };
  });
  renderChatWindow();

  fireEvent.change(screen.getByPlaceholderText("chat.input.placeholder"), { target: { value: "Hi" } });
  fireEvent.click(screen.getByRole("button", { name: "chat.button.send" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("LLM is down");
});
