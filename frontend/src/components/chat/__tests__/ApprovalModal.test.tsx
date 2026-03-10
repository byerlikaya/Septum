import { render, screen, fireEvent } from "@testing-library/react";
import { ApprovalModal, type ApprovalModalProps } from "../ApprovalModal";

// Simple i18n stub so we do not depend on real translations.
jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string, params?: Record<string, unknown>) => {
    if (key === "chat.approval.regulations") {
      return `regs: ${(params?.regs as string) ?? ""}`;
    }
    if (key === "chat.approval.noRegulations") return "no-regs";
    if (key === "chat.approval.timeRemaining") {
      return `time:${params?.seconds}`;
    }
    return key;
  }
}));

const baseChunks = [
  {
    id: 1,
    document_id: 10,
    text: "original text",
    source_page: 2,
    source_slide: null,
    source_sheet: null,
    source_timestamp_start: null,
    source_timestamp_end: null,
    section_title: "Section"
  }
];

function renderModal(override: Partial<ApprovalModalProps> = {}) {
  const onApprove = jest.fn();
  const onReject = jest.fn();
  const onClose = jest.fn();

  const props: ApprovalModalProps = {
    open: true,
    sessionId: "sess-1",
    maskedPrompt: "masked",
    chunks: baseChunks,
    activeRegulations: ["GDPR"],
    onApprove,
    onReject,
    onClose,
    timedOut: false,
    ...override
  };

  const result = render(<ApprovalModal {...props} />);
  return { ...result, onApprove, onReject, onClose };
}

it("renders masked prompt and regulation text when open", () => {
  renderModal();

  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByText("masked")).toBeInTheDocument();
  expect(screen.getByText(/regs:/)).toHaveTextContent("regs: GDPR");
});

it("does not render anything when open is false", () => {
  const { container } = renderModal({ open: false });
  expect(container).toBeEmptyDOMElement();
});

it("calls onApprove with edited chunks and closes", () => {
  const { onApprove, onClose } = renderModal();

  const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
  fireEvent.change(textarea, { target: { value: "edited text" } });

  const approveButton = screen.getByRole("button", { name: "chat.approval.button.approve" });
  fireEvent.click(approveButton);

  expect(onApprove).toHaveBeenCalledTimes(1);
  const [sessionId, chunks] = onApprove.mock.calls[0];
  expect(sessionId).toBe("sess-1");
  expect(chunks[0].text).toBe("edited text");
  expect(onClose).toHaveBeenCalled();
});

it("calls onReject and closes", () => {
  const { onReject, onClose } = renderModal();

  const rejectButton = screen.getByRole("button", { name: "chat.approval.button.reject" });
  fireEvent.click(rejectButton);

  expect(onReject).toHaveBeenCalledWith("sess-1");
  expect(onClose).toHaveBeenCalled();
});

