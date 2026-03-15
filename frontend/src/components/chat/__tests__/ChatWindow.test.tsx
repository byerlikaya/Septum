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
    streamChatAsk: (...args: any[]) => mockStreamChatAsk(...args),
    approvalApprove: (...args: any[]) => mockApprovalApprove(...args),
    approvalReject: (...args: any[]) => mockApprovalReject(...args),
    getChatDebug: (...args: any[]) => mockGetChatDebug(...args)
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
      activeRegulations={["GDPR"]}
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

