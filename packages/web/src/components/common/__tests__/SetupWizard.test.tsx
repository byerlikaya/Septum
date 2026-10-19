import { fireEvent, render, screen } from "@testing-library/react";
import { SetupWizard } from "../SetupWizard";
import { streamSSE } from "../../../lib/api";

const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPatch = jest.fn();

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/language", () => ({
  useLanguage: () => ({ language: "en", setLanguage: jest.fn() })
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  default: {
    get: (...a: unknown[]) => mockGet(...a),
    post: (...a: unknown[]) => mockPost(...a),
    patch: (...a: unknown[]) => mockPatch(...a)
  },
  PASSWORD_MIN_LENGTH: 12,
  PASSWORD_MAX_BYTES: 72,
  OLLAMA_URL_NOT_ALLOWED: "ollama_url_not_allowed",
  extractErrorCode: (e: { response?: { data?: { code?: string } } }) =>
    e?.response?.data?.code ?? null,
  extractErrorDetail: (e: { response?: { data?: { detail?: string } } }) =>
    e?.response?.data?.detail ?? null,
  initializeInfrastructure: jest.fn(),
  setAuthToken: jest.fn(),
  streamSSE: jest.fn(),
  testDatabaseConnection: jest.fn(),
  testRedisConnection: jest.fn()
}));

beforeEach(() => {
  jest.clearAllMocks();
  mockPatch.mockResolvedValue({ data: {} });
  mockGet.mockImplementation((url: string) =>
    url === "/api/regulations"
      ? Promise.resolve({
          data: [
            { id: "GDPR", display_name: "GDPR", region: "EU", is_active: true, is_builtin: true }
          ]
        })
      : Promise.resolve({ data: { installed: true } })
  );
  mockPost.mockImplementation((url: string) =>
    url === "/api/auth/register"
      ? Promise.resolve({ data: { access_token: "tok" } })
      : Promise.resolve({ data: { ok: true } })
  );
});

async function reachRegisterStep() {
  render(
    <SetupWizard
      startPhase="needs_application_setup"
      initialProvider="anthropic"
      initialModel="m"
      version="1"
      onComplete={jest.fn()}
    />
  );
  fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));
  fireEvent.click(await screen.findByText("setup.nav.next"));
  fireEvent.click(await screen.findByText("setup.step.whisper.installAndContinue"));
  await screen.findByText("setup.step.register.createAndFinish");
}

async function submitRegister(password: string, confirm: string = password) {
  await reachRegisterStep();
  fireEvent.change(document.querySelector<HTMLInputElement>("input[type=email]")!, {
    target: { value: "admin@example.com" }
  });
  const [pw, conf] = Array.from(
    document.querySelectorAll<HTMLInputElement>("input[type=password]")
  );
  fireEvent.change(pw, { target: { value: password } });
  fireEvent.change(conf, { target: { value: confirm } });
  fireEvent.click(screen.getByText("setup.step.register.createAndFinish"));
}

const registerCalls = () =>
  mockPost.mock.calls.filter((c) => c[0] === "/api/auth/register");

it("rejects a register password shorter than the minimum length", async () => {
  await submitRegister("short");
  expect(await screen.findByText("auth.changePassword.tooShort")).toBeInTheDocument();
  expect(registerCalls()).toHaveLength(0);
});

it("rejects a register password over 72 UTF-8 bytes even when under 72 characters", async () => {
  await submitRegister("ş".repeat(37));
  expect(await screen.findByText("auth.changePassword.tooLong")).toBeInTheDocument();
  expect(registerCalls()).toHaveLength(0);
});

it("still reports a mismatch before the length checks", async () => {
  await submitRegister("a".repeat(20), "b".repeat(20));
  expect(await screen.findByText("setup.step.register.mismatch")).toBeInTheDocument();
  expect(registerCalls()).toHaveLength(0);
});

it("registers a password of exactly 72 ASCII bytes", async () => {
  await submitRegister("a".repeat(72));
  await screen.findByText("setup.step.done.title");
  expect(registerCalls()).toHaveLength(1);
  expect(registerCalls()[0][1]).toEqual({
    email: "admin@example.com",
    password: "a".repeat(72)
  });
});

describe("provider step settings save", () => {
  const renderOllamaProvider = () =>
    render(
      <SetupWizard
        startPhase="needs_application_setup"
        initialProvider="ollama"
        initialModel="m"
        version="1"
        onComplete={jest.fn()}
      />
    );

  const testCalls = () =>
    mockPost.mock.calls.filter((c) => c[0] === "/api/settings/test-local-models");

  it("shows the translated rejection on the URL field and does not advance", async () => {
    mockPatch.mockRejectedValue({
      response: {
        status: 400,
        data: { detail: "English detail with host", code: "ollama_url_not_allowed" }
      }
    });
    renderOllamaProvider();

    fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("settings.llm.ollama.baseUrl.rejected");
    const input = screen.getByPlaceholderText("http://localhost:11434");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", alert.id);
    expect(screen.queryByText("English detail with host")).not.toBeInTheDocument();
    expect(testCalls()).toHaveLength(0);
    expect(screen.queryByText("setup.nav.next")).not.toBeInTheDocument();
  });

  it("clears the rejection when the URL is edited", async () => {
    mockPatch.mockRejectedValue({
      response: { data: { detail: "x", code: "ollama_url_not_allowed" } }
    });
    renderOllamaProvider();
    fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));
    await screen.findByRole("alert");

    fireEvent.change(screen.getByPlaceholderText("http://localhost:11434"), {
      target: { value: "http://localhost:11435" }
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the generic save failure and does not advance on other errors", async () => {
    mockPatch.mockRejectedValue(new Error("network down"));
    renderOllamaProvider();

    fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));

    expect(await screen.findByText("setup.step.provider.saveFailed")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(testCalls()).toHaveLength(0);
  });

  it("shows the server detail when a non-URL failure carries one", async () => {
    mockPatch.mockRejectedValue({
      response: { status: 422, data: { detail: "Unknown regulation id(s)" } }
    });
    renderOllamaProvider();

    fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));

    expect(await screen.findByText("Unknown regulation id(s)")).toBeInTheDocument();
    expect(testCalls()).toHaveLength(0);
  });
});

it("maps a coded cloud test-llm refusal to the translated rejection", async () => {
  mockPost.mockImplementation((url: string) =>
    url === "/api/settings/test-llm"
      ? Promise.reject({ response: { data: { detail: "English detail", code: "ollama_url_not_allowed" } } })
      : Promise.resolve({ data: { ok: true } })
  );
  render(
    <SetupWizard
      startPhase="needs_application_setup"
      initialProvider="anthropic"
      initialModel="m"
      version="1"
      onComplete={jest.fn()}
    />
  );

  fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));

  expect(await screen.findByText("settings.llm.ollama.baseUrl.rejected")).toBeInTheDocument();
  expect(screen.queryByText("setup.step.test.failed")).not.toBeInTheDocument();
});

async function reachWhisperStep() {
  render(
    <SetupWizard
      startPhase="needs_application_setup"
      initialProvider="anthropic"
      initialModel="m"
      version="1"
      onComplete={jest.fn()}
    />
  );
  fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));
  fireEvent.click(await screen.findByText("setup.nav.next"));
  return screen.findByText("setup.step.whisper.installAndContinue");
}

it("shows the translated refusal when the saved Whisper model is refused", async () => {
  const installButton = await reachWhisperStep();
  mockPatch.mockRejectedValueOnce({
    response: { data: { detail: "English detail", code: "whisper_model_not_allowed" } }
  });
  fireEvent.click(installButton);

  expect(await screen.findByRole("alert")).toHaveTextContent("errors.model.whisperNotAllowed");
});

it("shows the generic save failure and stays on the Whisper step when the save fails without a code", async () => {
  const installButton = await reachWhisperStep();
  mockPatch.mockRejectedValueOnce({ response: { data: { detail: "server text" } } });
  fireEvent.click(installButton);

  expect(await screen.findByRole("alert")).toHaveTextContent("setup.step.provider.saveFailed");
  expect(streamSSE).not.toHaveBeenCalled();
  expect(screen.queryByText("setup.step.register.createAndFinish")).not.toBeInTheDocument();
  expect(screen.getByText("setup.step.whisper.installAndContinue")).toBeInTheDocument();
});

it("shows the translated refusal when the install stream answers with an HTTP error", async () => {
  const baseGet = mockGet.getMockImplementation();
  mockGet.mockImplementation((url: string) =>
    url.startsWith("/api/setup/whisper-status")
      ? Promise.resolve({ data: { installed: false } })
      : baseGet!(url)
  );
  (streamSSE as jest.Mock).mockImplementation(
    (_url: string, _body: unknown, _onLine: unknown, options: { onHttpError: (code: string) => void }) => {
      options.onHttpError("whisper_model_not_allowed");
      return { abort: jest.fn() };
    }
  );
  const installButton = await reachWhisperStep();
  fireEvent.click(installButton);

  expect(await screen.findByRole("alert")).toHaveTextContent("errors.model.whisperNotAllowed");
});
