import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MessageBubble } from "../MessageBubble";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

Object.assign(navigator, {
  clipboard: {
    writeText: jest.fn().mockResolvedValue(undefined)
  }
});

it("renders user and assistant messages differently", () => {
  const userMsg = { id: "u1", role: "user" as const, content: "Hi" };
  const assistantMsg = { id: "a1", role: "assistant" as const, content: "Hello" };

  const { rerender } = render(<MessageBubble message={userMsg} />);
  expect(screen.getByText("Hi")).toBeInTheDocument();

  rerender(<MessageBubble message={assistantMsg} />);
  expect(screen.getByText("Hello")).toBeInTheDocument();
});

it("shows thinking animation when isThinking is true for assistant", () => {
  const msg = { id: "a1", role: "assistant" as const, content: "" };
  render(<MessageBubble message={msg} isThinking={true} />);
  expect(screen.getByText("chat.status.thinking")).toBeInTheDocument();
});

it("copies assistant message content to clipboard and shows copied state", async () => {
  const msg = { id: "a1", role: "assistant" as const, content: "Copy me" };
  render(<MessageBubble message={msg} />);

  const copyButton = screen.getByRole("button", { name: "chat.copyAnswer" });
  fireEvent.click(copyButton);

  await waitFor(() => {
    expect(screen.getByText("chat.copied")).toBeInTheDocument();
  });
});

it("calls onDebugClick when debug button is pressed", () => {
  const onDebugClick = jest.fn();
  const msg = {
    id: "a1",
    role: "assistant" as const,
    content: "Answer",
    sessionId: "sess-123"
  };

  render(<MessageBubble message={msg} onDebugClick={onDebugClick} />);

  const debugButton = screen.getByRole("button", { name: "chat.debug.button" });
  fireEvent.click(debugButton);

  expect(onDebugClick).toHaveBeenCalledWith("sess-123");
});

