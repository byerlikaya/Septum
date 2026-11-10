import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import SettingsPage from "../page";

const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPatch = jest.fn();
const mockStatus = jest.fn();
const mockT = (key: string) => key;

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => mockT
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  default: {
    get: (...a: unknown[]) => mockGet(...a),
    post: (...a: unknown[]) => mockPost(...a),
    patch: (...a: unknown[]) => mockPatch(...a)
  },
  getContextualLayerStatus: (...a: unknown[]) => mockStatus(...a),
  OLLAMA_URL_NOT_ALLOWED: "ollama_url_not_allowed",
  extractErrorCode: (e: { response?: { data?: { code?: string } } }) =>
    e?.response?.data?.code ?? null,
  extractErrorDetail: (e: { response?: { data?: { detail?: string } } }) =>
    e?.response?.data?.detail ?? null
}));

jest.mock("../../../components/common/OllamaModelCombobox", () => ({
  OllamaModelCombobox: () => null
}));

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(console, "error").mockImplementation(() => undefined);
  mockStatus.mockResolvedValue({ state: "covered", entity_types: [], local_model_reachable: true });
  mockGet.mockResolvedValue({
    data: { llm_provider: "anthropic", llm_model: "m", ollama_base_url: "http://localhost:11434" }
  });
});

afterEach(() => {
  jest.restoreAllMocks();
});

it("maps a coded cloud test refusal to the translated rejection instead of the English detail", async () => {
  mockPost.mockRejectedValue({
    response: { data: { detail: "English detail with host", code: "ollama_url_not_allowed" } }
  });
  render(<SettingsPage />);

  fireEvent.click(await screen.findByText("settings.common.testConnection"));

  expect(await screen.findByText("settings.llm.ollama.baseUrl.rejected")).toBeInTheDocument();
  expect(screen.queryByText("English detail with host")).not.toBeInTheDocument();
});

it("maps llm_unavailable from the cloud test to the translated message instead of the English detail", async () => {
  mockPost.mockRejectedValue({
    response: { status: 400, data: { detail: "English backend text", code: "llm_unavailable" } }
  });
  render(<SettingsPage />);

  fireEvent.click(await screen.findByText("settings.common.testConnection"));

  expect(await screen.findByText("settings.llm.test.unavailable")).toBeInTheDocument();
  expect(screen.queryByText("English backend text")).not.toBeInTheDocument();
});

it("maps ollama_url_not_allowed with a 400 status to its existing key", async () => {
  mockPost.mockRejectedValue({
    response: { status: 400, data: { detail: "English backend text", code: "ollama_url_not_allowed" } }
  });
  render(<SettingsPage />);

  fireEvent.click(await screen.findByText("settings.common.testConnection"));

  expect(await screen.findByText("settings.llm.ollama.baseUrl.rejected")).toBeInTheDocument();
  expect(screen.queryByText("English backend text")).not.toBeInTheDocument();
});

it("shows the server detail when the refusal carries no known code", async () => {
  mockPost.mockRejectedValue({
    response: { status: 400, data: { detail: "Plain server text" } }
  });
  render(<SettingsPage />);

  fireEvent.click(await screen.findByText("settings.common.testConnection"));

  expect(await screen.findByText("Plain server text")).toBeInTheDocument();
});

it("shows the update error with a retry that reloads the stored value when saving a field fails", async () => {
  mockGet.mockResolvedValue({
    data: {
      llm_provider: "anthropic",
      llm_model: "m",
      ollama_base_url: "http://localhost:11434",
      chunk_size: 1000,
      chunk_overlap: 100,
      top_k_retrieval: 5,
      rag_relevance_threshold: 0.3,
      pdf_chunk_size: 800,
      audio_chunk_size: 600,
      spreadsheet_chunk_size: 500
    }
  });
  mockPatch.mockRejectedValue({ response: { status: 422, data: { detail: "nope" } } });
  render(<SettingsPage />);

  fireEvent.click(await screen.findByText("settings.tabs.rag.label"));
  const input = (await screen.findByText("settings.rag.defaultChunkSize.label"))
    .parentElement!.querySelector("input")!;
  fireEvent.change(input, { target: { value: "2000" } });
  fireEvent.blur(input);

  expect(await screen.findByText("errors.settings.update")).toBeInTheDocument();
  expect(mockPatch).toHaveBeenCalledWith("/api/settings", { chunk_size: 2000 });
  // The error replaces the form with a retry action, so the optimistic value is
  // never shown again; retry reloads the stored settings.
  expect(input).not.toBeInTheDocument();
  mockGet.mockClear();
  fireEvent.click(screen.getByText("common.retry"));
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/settings"));
  fireEvent.click(await screen.findByText("settings.tabs.rag.label"));
  const reloaded = (await screen.findByText("settings.rag.defaultChunkSize.label"))
    .parentElement!.querySelector("input")!;
  expect(reloaded).toHaveValue(1000);
});

describe("contextual coverage banner", () => {
  it("shows the banner above every tab and opens the Privacy tab from the turn-on action (layer off, model now reachable)", async () => {
    mockStatus.mockResolvedValue({
      state: "layer_off",
      entity_types: ["DIAGNOSIS", "RELIGION"],
      local_model_reachable: true
    });
    render(<SettingsPage />);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "settings.contextualCoverage.layerOff.reachable"
    );
    expect(screen.getByRole("status")).toHaveTextContent("DIAGNOSIS, RELIGION");

    fireEvent.click(screen.getByText("settings.tabs.rag.label"));
    expect(screen.getByRole("status")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "settings.contextualCoverage.turnOn" }));
    expect(screen.getByText("settings.tabs.privacy.label").closest("button")).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(mockStatus).toHaveBeenCalledTimes(1);
  });

  it("renders an alert when the local model is unavailable", async () => {
    mockStatus.mockResolvedValue({
      state: "local_model_unavailable",
      entity_types: ["DIAGNOSIS"],
      local_model_reachable: false
    });
    render(<SettingsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "settings.contextualCoverage.modelUnavailable"
    );
  });

  it("moves focus to the LLM Provider tab panel after the set-up action switches tabs", async () => {
    mockStatus.mockResolvedValue({
      state: "layer_off",
      entity_types: ["DIAGNOSIS"],
      local_model_reachable: false
    });
    render(<SettingsPage />);

    fireEvent.click(await screen.findByText("settings.tabs.privacy.label"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "settings.contextualCoverage.layerOff.unreachable"
    );
    const setUp = screen.getByRole("button", { name: "settings.contextualCoverage.setUpModel" });
    setUp.focus();
    fireEvent.click(setUp);

    expect(screen.getByText("settings.tabs.llm.label").closest("button")).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    await waitFor(() => {
      const active = document.activeElement as HTMLElement;
      expect(active).not.toBe(setUp);
      expect(active).toHaveAttribute("tabindex", "-1");
      expect(active).toContainElement(screen.getByText("settings.common.testConnection"));
    });
  });

  it.each(["covered", "not_applicable"])("stays silent after the call resolves for %s", async (state) => {
    mockStatus.mockResolvedValue({ state, entity_types: [], local_model_reachable: true });
    render(<SettingsPage />);

    expect(await screen.findByText("settings.common.testConnection")).toBeInTheDocument();
    await waitFor(() => expect(mockStatus).toHaveBeenCalledTimes(1));
    await mockStatus.mock.results[0].value;
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a neutral notice without blocking the page when the status call fails", async () => {
    mockStatus.mockRejectedValue(new Error("down"));
    render(<SettingsPage />);

    expect(await screen.findByText("settings.common.testConnection")).toBeInTheDocument();
    expect(await screen.findByRole("status")).toHaveTextContent(
      "settings.contextualCoverage.unverified"
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
