import { fireEvent, render, screen } from "@testing-library/react";
import ChatPage from "../page";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  default: { get: jest.fn() },
  getDocuments: jest.fn().mockResolvedValue([]),
  getSettings: jest.fn().mockResolvedValue({}),
  getRegulations: jest.fn().mockResolvedValue([]),
  listChatSessions: jest.fn().mockResolvedValue([]),
  createChatSession: jest.fn(),
  getChatSession: jest.fn(),
  deleteChatSession: jest.fn(),
  addChatMessage: jest.fn(),
  convertRejectedToApproved: jest.fn(),
  updateChatSession: jest.fn(),
  sendFrontendError: jest.fn()
}));

jest.mock("../../../components/chat/ChatHistory", () => ({
  ChatHistory: () => <div data-testid="chat-history" />
}));
jest.mock("../../../components/chat/ChatWindow", () => ({ ChatWindow: () => null }));
jest.mock("../../../components/chat/DocumentSelector", () => ({ DocumentSelector: () => null }));
jest.mock("../../../components/chat/DocumentSelectionNotice", () => ({
  DocumentSelectionNotice: () => null
}));
jest.mock("../../../components/chat/DeanonymizationBanner", () => ({
  DeanonymizationBanner: () => null
}));
jest.mock("../../../components/common/BlockingLoader", () => ({ BlockingLoader: () => null }));

describe("ChatPage history toggle", () => {
  it("renders a single ChatHistory", async () => {
    render(<ChatPage />);
    await screen.findByRole("button", { name: "chat.history.title" });
    expect(screen.getAllByTestId("chat-history")).toHaveLength(1);
  });

  it("starts collapsed and expands on click", async () => {
    render(<ChatPage />);
    const toggle = await screen.findByRole("button", { name: "chat.history.title" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    const panel = document.getElementById(toggle.getAttribute("aria-controls") as string);
    expect(panel).toHaveClass("hidden");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(panel).toHaveClass("block");
    expect(panel).not.toHaveClass("hidden");
  });
});
