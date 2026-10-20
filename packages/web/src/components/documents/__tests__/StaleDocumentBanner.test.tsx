import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StaleDocumentBanner } from "../StaleDocumentBanner";
import { CurrentUserProvider } from "../../../lib/currentUser";
import type { AuthUser, Document, UserRole } from "@/lib/types";

const mockReprocess = jest.fn();
const mockSendFrontendError = jest.fn();

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  reprocessDocument: (...args: unknown[]) => mockReprocess(...args),
  sendFrontendError: (...args: unknown[]) => mockSendFrontendError(...args)
}));

function staleDocument(overrides: Partial<Document> = {}): Document {
  return {
    id: 7,
    filename: "contract.pdf",
    original_filename: "contract.pdf",
    file_type: "application/pdf",
    file_format: "pdf",
    detected_language: "en",
    language_override: null,
    uploaded_at: "2026-10-19T00:00:00Z",
    encrypted_path: "enc/7",
    chunk_count: 3,
    entity_count: 2,
    ingestion_status: "completed",
    ingestion_error: null,
    ingestion_error_code: null,
    file_size_bytes: 2048,
    transcription_text: null,
    ocr_confidence: null,
    active_regulation_ids: [],
    needs_reprocessing: true,
    missing_entity_types: ["EMAIL"],
    sanitized_policy_known: true,
    ...overrides,
  };
}

function renderAs(role: UserRole | null, ui: React.ReactElement) {
  const user: AuthUser | null =
    role === null ? null : { id: 1, email: "user@example.com", role, is_active: true };
  return render(<CurrentUserProvider value={user}>{ui}</CurrentUserProvider>);
}

const reprocessButton = () => screen.queryByRole("button", { name: "documents.actions.reprocess" });

beforeEach(() => {
  jest.clearAllMocks();
});

it("explains the stale chunks and reprocesses the document in one click for an editor", async () => {
  const updated = staleDocument({ ingestion_status: "processing", needs_reprocessing: false });
  mockReprocess.mockResolvedValue(updated);
  const onReprocessed = jest.fn();

  renderAs("editor", <StaleDocumentBanner doc={staleDocument()} onReprocessed={onReprocessed} />);

  expect(screen.getByText("documents.stale.banner")).toBeInTheDocument();
  fireEvent.click(reprocessButton()!);

  await waitFor(() => expect(onReprocessed).toHaveBeenCalledWith(updated));
  expect(mockReprocess).toHaveBeenCalledTimes(1);
  expect(mockReprocess).toHaveBeenCalledWith(7);
});

it("shows the reprocess action to an admin", () => {
  renderAs("admin", <StaleDocumentBanner doc={staleDocument()} />);
  expect(reprocessButton()).toBeInTheDocument();
});

it("hides the reprocess action from viewers but keeps the explanation", () => {
  renderAs("viewer", <StaleDocumentBanner doc={staleDocument()} />);
  expect(screen.getByText("documents.stale.banner")).toBeInTheDocument();
  expect(reprocessButton()).not.toBeInTheDocument();
  expect(mockReprocess).not.toHaveBeenCalled();
});

it("hides the reprocess action when no user is resolved", () => {
  renderAs(null, <StaleDocumentBanner doc={staleDocument()} />);
  expect(reprocessButton()).not.toBeInTheDocument();
});

it("disables the reprocess action while the document is already processing", () => {
  renderAs("editor", <StaleDocumentBanner doc={staleDocument({ ingestion_status: "processing" })} />);
  expect(reprocessButton()).toBeDisabled();
});

it("shows an alert and reports the failure when reprocessing fails", async () => {
  mockReprocess.mockRejectedValue(new Error("network"));
  const onReprocessed = jest.fn();

  renderAs("editor", <StaleDocumentBanner doc={staleDocument()} onReprocessed={onReprocessed} />);
  fireEvent.click(reprocessButton()!);

  expect(await screen.findByRole("alert")).toHaveTextContent("errors.documents.reprocess");
  expect(onReprocessed).not.toHaveBeenCalled();
  expect(mockSendFrontendError).toHaveBeenCalledWith(
    expect.objectContaining({ level: "ERROR", extra: { document_id: 7 } })
  );
});
