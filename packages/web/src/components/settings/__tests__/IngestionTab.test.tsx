import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { IngestionTab } from "../IngestionTab";

const mockGet = jest.fn();
const mockPost = jest.fn();

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  default: {
    get: (...a: unknown[]) => mockGet(...a),
    post: (...a: unknown[]) => mockPost(...a)
  },
  OLLAMA_URL_NOT_ALLOWED: "ollama_url_not_allowed",
  extractErrorCode: (e: { response?: { data?: { code?: string } } }) =>
    e?.response?.data?.code ?? null,
  extractErrorDetail: (e: { response?: { data?: { detail?: string } } }) =>
    e?.response?.data?.detail ?? null
}));

const props = {
  settings: { image_ocr_languages: ["en"] } as never,
  onChange: jest.fn(),
  isSaving: () => false
};

const health = {
  ffmpeg: "ok",
  whisper_package: "ok",
  whisper_model: "missing"
};

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(console, "error").mockImplementation(() => {});
  mockGet.mockResolvedValue({ data: health });
});

afterEach(() => {
  jest.restoreAllMocks();
});

async function clickInstall() {
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  fireEvent.click(screen.getByText("settings.ingestion.audioHealth.installButton"));
}

it("shows the install-in-progress message when the server answers 409", async () => {
  mockPost.mockRejectedValue({ isAxiosError: true, response: { status: 409 } });
  render(<IngestionTab {...props} />);
  await clickInstall();
  expect(
    await screen.findByText("settings.ingestion.health.installInProgress")
  ).toBeInTheDocument();
});

it("shows the generic install failure for other errors", async () => {
  mockPost.mockRejectedValue({ isAxiosError: true, response: { status: 500 } });
  render(<IngestionTab {...props} />);
  await clickInstall();
  expect(
    await screen.findByText("settings.ingestion.health.installFailed")
  ).toBeInTheDocument();
});

it("shows the translated refusal when the install is refused with whisper_model_not_allowed", async () => {
  mockPost.mockRejectedValue({
    isAxiosError: true,
    response: { status: 400, data: { detail: "English", code: "whisper_model_not_allowed" } }
  });
  render(<IngestionTab {...props} />);
  await clickInstall();
  expect(
    await screen.findByText("errors.model.whisperNotAllowed")
  ).toBeInTheDocument();
});

it("shows the Whisper refusal inline at the select with aria attributes", async () => {
  const onChange = jest.fn().mockResolvedValue({
    response: { data: { detail: "English", code: "whisper_model_not_allowed" } }
  });
  render(
    <IngestionTab
      {...props}
      settings={{ image_ocr_languages: ["en"], whisper_model: "base" } as never}
      onChange={onChange}
    />
  );
  const select = screen.getByDisplayValue("base (≈142 MB)");
  fireEvent.change(select, { target: { value: "tiny" } });

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("errors.model.whisperNotAllowed");
  expect(onChange).toHaveBeenCalledWith("whisper_model", "tiny");
  expect(select).toHaveAttribute("aria-invalid", "true");
  expect(select).toHaveAttribute("aria-describedby", "whisper-model-message");
});

it("offers only registry Whisper names, never a saved non-registry value", () => {
  render(
    <IngestionTab
      {...props}
      settings={{ image_ocr_languages: ["en"], whisper_model: "/local/custom-model.pt" } as never}
    />
  );
  expect(screen.queryByRole("option", { name: "/local/custom-model.pt" })).not.toBeInTheDocument();
  expect(screen.getByRole("option", { name: "base (≈142 MB)" })).toBeInTheDocument();
});

it("shows a placeholder and a linked warning for an unsupported saved Whisper model", () => {
  const { container } = render(
    <IngestionTab
      {...props}
      settings={{ image_ocr_languages: ["en"], whisper_model: "/local/custom-model.pt" } as never}
    />
  );
  const select = container.querySelector("select") as HTMLSelectElement;
  const warning = screen.getByText("settings.ingestion.whisperModel.unsupportedWarning");
  const placeholder = screen.getByRole("option", {
    name: "settings.ingestion.whisperModel.unsupportedPlaceholder",
  });

  expect(select).toHaveValue("");
  expect(placeholder).toBeDisabled();
  expect(select.selectedOptions[0]).toBe(placeholder);
  expect(select.querySelectorAll("option")).toHaveLength(6);
  expect(warning).not.toHaveAttribute("role");
  expect(select.getAttribute("aria-describedby")?.split(" ")).toContain(warning.id);
});

it("shows no placeholder or warning when the saved Whisper model is supported", () => {
  render(
    <IngestionTab
      {...props}
      settings={{ image_ocr_languages: ["en"], whisper_model: "small" } as never}
    />
  );
  expect(
    screen.queryByText("settings.ingestion.whisperModel.unsupportedWarning")
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("option", { name: "settings.ingestion.whisperModel.unsupportedPlaceholder" })
  ).not.toBeInTheDocument();
});
