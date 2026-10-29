import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SetupWizard } from "../SetupWizard";
import { streamSSE, testDatabaseConnection } from "../../../lib/api";

const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPatch = jest.fn();
const mockVerifySetupToken = jest.fn();
const mockSetSetupToken = jest.fn();
const mockClearSetupToken = jest.fn();
const mockOnSetupForbidden = jest.fn();
const mockProbeOllama = jest.fn();

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
  clearSetupToken: (...a: unknown[]) => mockClearSetupToken(...a),
  setSetupToken: (...a: unknown[]) => mockSetSetupToken(...a),
  verifySetupToken: (...a: unknown[]) => mockVerifySetupToken(...a),
  onSetupForbidden: (...a: unknown[]) => mockOnSetupForbidden(...a),
  probeOllama: (...a: unknown[]) => mockProbeOllama(...a),
  streamSSE: jest.fn(),
  testDatabaseConnection: jest.fn(),
  testRedisConnection: jest.fn()
}));

beforeEach(() => {
  jest.clearAllMocks();
  mockPatch.mockResolvedValue({ data: {} });
  mockVerifySetupToken.mockResolvedValue(undefined);
  mockOnSetupForbidden.mockImplementation(() => () => {});
  mockProbeOllama.mockResolvedValue({ url: null, models: [], default_url: null });
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

async function passSetupToken(nextMarker: string) {
  fireEvent.change(screen.getByLabelText("setup.step.token.label"), {
    target: { value: "setup-token-value" }
  });
  fireEvent.click(screen.getByText("setup.step.token.verify"));
  await screen.findByText(nextMarker);
}

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
  await passSetupToken("setup.step.provider.testAndContinue");
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

function rejectRegisterWith(status: number, data: unknown, headers: Record<string, unknown> = {}) {
  mockPost.mockImplementation((url: string) =>
    url === "/api/auth/register"
      ? Promise.reject({ response: { status, data, headers } })
      : Promise.resolve({ data: { ok: true } })
  );
}

it("shows the busy message on register 503 server_busy and not the raw detail", async () => {
  rejectRegisterWith(
    503,
    { detail: "Server is busy, try again shortly.", code: "server_busy" },
    { "retry-after": "2" }
  );
  await submitRegister("a".repeat(20));
  expect(await screen.findByText("auth.error.serverBusy")).toBeInTheDocument();
  expect(screen.queryByText("Server is busy, try again shortly.")).toBeNull();
  expect(screen.getByText("setup.step.register.createAndFinish")).toBeInTheDocument();
});

it("shows the rate-limit message on register 429 and not the raw detail", async () => {
  rejectRegisterWith(429, { detail: "Too many registrations." }, { "retry-after": "45" });
  await submitRegister("a".repeat(20));
  expect(await screen.findByText("auth.error.rateLimited")).toBeInTheDocument();
  expect(screen.queryByText("Too many registrations.")).toBeNull();
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
  const renderOllamaProvider = async () => {
    render(
      <SetupWizard
        startPhase="needs_application_setup"
        initialProvider="ollama"
        initialModel="m"
        version="1"
        onComplete={jest.fn()}
      />
    );
    await passSetupToken("setup.step.provider.testAndContinue");
  };

  const testCalls = () =>
    mockPost.mock.calls.filter((c) => c[0] === "/api/settings/test-local-models");

  it("shows the translated rejection on the URL field and does not advance", async () => {
    mockPatch.mockRejectedValue({
      response: {
        status: 400,
        data: { detail: "English detail with host", code: "ollama_url_not_allowed" }
      }
    });
    await renderOllamaProvider();

    fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("settings.llm.ollama.baseUrl.rejected");
    const input = screen.getByPlaceholderText("settings.llm.ollama.baseUrl.placeholder");
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
    await renderOllamaProvider();
    fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));
    await screen.findByRole("alert");

    fireEvent.change(screen.getByPlaceholderText("settings.llm.ollama.baseUrl.placeholder"), {
      target: { value: "http://localhost:11435" }
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the generic save failure and does not advance on other errors", async () => {
    mockPatch.mockRejectedValue(new Error("network down"));
    await renderOllamaProvider();

    fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));

    expect(await screen.findByText("setup.step.provider.saveFailed")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(testCalls()).toHaveLength(0);
  });

  it("shows the server detail when a non-URL failure carries one", async () => {
    mockPatch.mockRejectedValue({
      response: { status: 422, data: { detail: "Unknown regulation id(s)" } }
    });
    await renderOllamaProvider();

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
  await passSetupToken("setup.step.provider.testAndContinue");

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
  await passSetupToken("setup.step.provider.testAndContinue");
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

describe("setup token step", () => {
  const renderWizard = (startPhase: "needs_application_setup" | "needs_infrastructure" = "needs_application_setup") =>
    render(
      <SetupWizard
        startPhase={startPhase}
        initialProvider="anthropic"
        initialModel="m"
        version="1"
        onComplete={jest.fn()}
      />
    );
  const tokenInput = () => screen.getByLabelText("setup.step.token.label");
  const verifyButton = () => screen.getByText("setup.step.token.verify");
  const rejectWith = (status: number, code?: string) =>
    Promise.reject({ response: { status, data: code ? { detail: "Setup access denied.", code } : {} } });

  it("keeps the Verify button disabled until a token is entered", () => {
    renderWizard();
    expect(verifyButton()).toBeDisabled();
    expect(screen.queryByText("setup.step.provider.testAndContinue")).not.toBeInTheDocument();
    expect(mockVerifySetupToken).not.toHaveBeenCalled();
  });

  it("blocks progress until verify-token resolves, then stores the token and advances", async () => {
    let resolveVerify!: () => void;
    mockVerifySetupToken.mockReturnValueOnce(new Promise<void>((resolve) => { resolveVerify = resolve; }));
    renderWizard();

    fireEvent.change(tokenInput(), { target: { value: "tok" } });
    fireEvent.click(verifyButton());

    expect(await screen.findByText("setup.step.test.pending")).toBeInTheDocument();
    expect(screen.queryByText("setup.step.provider.testAndContinue")).not.toBeInTheDocument();
    expect(mockSetSetupToken).not.toHaveBeenCalled();

    resolveVerify();

    expect(await screen.findByText("setup.step.provider.testAndContinue")).toBeInTheDocument();
    expect(mockVerifySetupToken).toHaveBeenCalledWith("tok");
    expect(mockSetSetupToken).toHaveBeenCalledWith("tok");
  });

  it("shows the invalid-token message on 403 and does not advance or store the token", async () => {
    mockVerifySetupToken.mockImplementationOnce(() => rejectWith(403, "setup_forbidden"));
    renderWizard();

    fireEvent.change(tokenInput(), { target: { value: "wrong" } });
    fireEvent.click(verifyButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("setup.step.token.invalid");
    expect(tokenInput()).toHaveAttribute("aria-invalid", "true");
    expect(tokenInput()).toHaveAttribute("aria-describedby", expect.stringContaining(alert.id));
    expect(mockClearSetupToken).toHaveBeenCalled();
    expect(mockSetSetupToken).not.toHaveBeenCalled();
    expect(screen.queryByText("setup.step.provider.testAndContinue")).not.toBeInTheDocument();
  });

  it("shows the too-many-attempts message on 429 and does not advance", async () => {
    mockVerifySetupToken.mockImplementationOnce(() => rejectWith(429));
    renderWizard();

    fireEvent.change(tokenInput(), { target: { value: "tok" } });
    fireEvent.click(verifyButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("setup.step.token.tooManyAttempts");
    expect(screen.queryByText("setup.step.token.invalid")).not.toBeInTheDocument();
    expect(mockSetSetupToken).not.toHaveBeenCalled();
  });

  it("shows the generic failure when verification cannot reach the server", async () => {
    mockVerifySetupToken.mockImplementationOnce(() => Promise.reject(new Error("network down")));
    renderWizard();

    fireEvent.change(tokenInput(), { target: { value: "tok" } });
    fireEvent.click(verifyButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("setup.step.token.failed");
    expect(mockClearSetupToken).not.toHaveBeenCalled();
  });

  it("returns to the token step with the invalid message on a setup-gate 403, then resumes where it left off", async () => {
    renderWizard();
    await passSetupToken("setup.step.provider.testAndContinue");

    const listener = mockOnSetupForbidden.mock.calls[mockOnSetupForbidden.mock.calls.length - 1][0];
    act(() => listener());

    expect(await screen.findByRole("alert")).toHaveTextContent("setup.step.token.invalid");
    expect(tokenInput()).toHaveValue("");
    expect(screen.queryByText("setup.step.provider.testAndContinue")).not.toBeInTheDocument();

    await passSetupToken("setup.step.provider.testAndContinue");
  });

  it("returns to the token step when a database test is gated", async () => {
    renderWizard("needs_infrastructure");
    await passSetupToken("setup.welcome.start");
    fireEvent.click(screen.getByText("setup.welcome.start"));
    fireEvent.click(screen.getByText("setup.step.database.postgresql"));
    fireEvent.change(screen.getByPlaceholderText("setup.step.database.url.placeholder"), {
      target: { value: "postgresql://u:p@h/db" }
    });
    (testDatabaseConnection as jest.Mock).mockImplementationOnce(() => {
      const listener = mockOnSetupForbidden.mock.calls[mockOnSetupForbidden.mock.calls.length - 1][0];
      act(() => listener());
      return rejectWith(403, "setup_forbidden");
    });

    fireEvent.click(screen.getByText("setup.step.database.test"));

    expect(await screen.findByRole("alert")).toHaveTextContent("setup.step.token.invalid");
    expect(screen.queryByText("setup.step.database.test")).not.toBeInTheDocument();
  });

  it("translates connection codes instead of showing the server message", async () => {
    renderWizard("needs_infrastructure");
    await passSetupToken("setup.welcome.start");
    fireEvent.click(screen.getByText("setup.welcome.start"));
    fireEvent.click(screen.getByText("setup.step.database.postgresql"));
    fireEvent.change(screen.getByPlaceholderText("setup.step.database.url.placeholder"), {
      target: { value: "mysql://u:p@h/db" }
    });

    (testDatabaseConnection as jest.Mock).mockResolvedValueOnce({
      ok: false,
      message: "server text one",
      code: "unsupported_url_scheme"
    });
    fireEvent.click(screen.getByText("setup.step.database.test"));
    expect(await screen.findByText("setup.step.connection.unsupportedScheme")).toBeInTheDocument();
    expect(screen.queryByText("server text one")).not.toBeInTheDocument();

    (testDatabaseConnection as jest.Mock).mockResolvedValueOnce({
      ok: false,
      message: "server text two",
      code: "connection_failed"
    });
    fireEvent.click(screen.getByText("setup.step.database.test"));
    expect(await screen.findByText("setup.step.database.test.failed")).toBeInTheDocument();
    expect(screen.queryByText("server text two")).not.toBeInTheDocument();
  });

  it("unsubscribes from the setup gate on unmount", () => {
    const unsubscribe = jest.fn();
    mockOnSetupForbidden.mockReturnValue(unsubscribe);
    const { unmount } = renderWizard();

    unmount();

    expect(unsubscribe).toHaveBeenCalled();
  });

  it("clears the setup token once the admin account is created", async () => {
    await submitRegister("a".repeat(72));
    await screen.findByText("setup.step.done.title");

    expect(mockClearSetupToken).toHaveBeenCalledTimes(1);
  });
});

describe("setup gate on the regulations path", () => {
  const forbidden = () =>
    Promise.reject({ response: { status: 403, data: { detail: "Setup access denied.", code: "setup_forbidden" } } });
  const regulationsWithOneInactive = () =>
    Promise.resolve({
      data: [
        { id: "GDPR", display_name: "GDPR", region: "EU", is_active: true, is_builtin: true },
        { id: "KVKK", display_name: "KVKK", region: "TR", is_active: false, is_builtin: true }
      ]
    });
  const activationCalls = () =>
    mockPatch.mock.calls.filter((c) => String(c[0]).startsWith("/api/regulations/"));

  async function reachRegulationsStep() {
    mockGet.mockImplementation((url: string) =>
      url === "/api/regulations" ? regulationsWithOneInactive() : Promise.resolve({ data: { installed: true } })
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
    await passSetupToken("setup.step.provider.testAndContinue");
    fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));
    return screen.findByText("setup.nav.next");
  }

  it("does not open the regulations step when listing regulations is gated", async () => {
    mockGet.mockImplementation((url: string) =>
      url === "/api/regulations" ? forbidden() : Promise.resolve({ data: { installed: true } })
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
    await passSetupToken("setup.step.provider.testAndContinue");

    fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));

    expect(await screen.findByText("setup.step.provider.testAndContinue")).not.toBeDisabled();
    expect(mockGet).toHaveBeenCalledWith("/api/regulations");
    expect(screen.queryByText("setup.step.regulations")).not.toBeInTheDocument();
  });

  it("does not advance to the whisper step when an activation is gated", async () => {
    mockPatch.mockImplementation((url: string) =>
      String(url).startsWith("/api/regulations/") ? forbidden() : Promise.resolve({ data: {} })
    );
    fireEvent.click(await reachRegulationsStep());

    await expect(
      screen.findByText("setup.step.whisper.installAndContinue", {}, { timeout: 300 })
    ).rejects.toThrow();
    expect(activationCalls()).toEqual([["/api/regulations/KVKK/activate", { is_active: true }]]);
    expect(screen.getByText("setup.step.regulations")).toBeInTheDocument();
  });

  it("still advances to the whisper step when an activation fails for another reason", async () => {
    mockPatch.mockImplementation((url: string) =>
      String(url).startsWith("/api/regulations/")
        ? Promise.reject({ response: { status: 500, data: { detail: "boom" } } })
        : Promise.resolve({ data: {} })
    );
    fireEvent.click(await reachRegulationsStep());

    expect(await screen.findByText("setup.step.whisper.installAndContinue")).toBeInTheDocument();
    expect(activationCalls()).toHaveLength(1);
  });
});

it("translates a rejected unsupported-scheme database test (HTTP 400)", async () => {
  render(
    <SetupWizard
      startPhase="needs_infrastructure"
      initialProvider="anthropic"
      initialModel="m"
      version="1"
      onComplete={jest.fn()}
    />
  );
  await passSetupToken("setup.welcome.start");
  fireEvent.click(screen.getByText("setup.welcome.start"));
  fireEvent.click(screen.getByText("setup.step.database.postgresql"));
  fireEvent.change(screen.getByPlaceholderText("setup.step.database.url.placeholder"), {
    target: { value: "mysql://u:p@h/db" }
  });
  (testDatabaseConnection as jest.Mock).mockImplementationOnce(() =>
    Promise.reject({
      response: { status: 400, data: { detail: "Unsupported connection URL scheme.", code: "unsupported_url_scheme" } }
    })
  );

  fireEvent.click(screen.getByText("setup.step.database.test"));

  expect(await screen.findByText("setup.step.connection.unsupportedScheme")).toBeInTheDocument();
  expect(screen.queryByText("Unsupported connection URL scheme.")).not.toBeInTheDocument();
});

describe("Ollama URL prefill", () => {
  const renderProvider = async (initialProvider: string) => {
    render(
      <SetupWizard
        startPhase="needs_application_setup"
        initialProvider={initialProvider}
        initialModel="m"
        version="1"
        onComplete={jest.fn()}
      />
    );
    await passSetupToken("setup.step.provider.testAndContinue");
  };
  const urlInput = () =>
    screen.getByPlaceholderText("settings.llm.ollama.baseUrl.placeholder") as HTMLInputElement;
  const patchedPayload = async () => {
    fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));
    await act(async () => {});
    expect(mockPatch).toHaveBeenCalledWith("/api/settings", expect.any(Object));
    return mockPatch.mock.calls[0][1] as Record<string, string>;
  };

  it("uses the server default when the probe finds nothing", async () => {
    mockProbeOllama.mockResolvedValue({
      url: null, models: [], default_url: "http://host.docker.internal:11434"
    });
    await renderProvider("ollama");
    await waitFor(() => expect(urlInput().value).toBe("http://host.docker.internal:11434"));
    expect((await patchedPayload()).ollama_base_url).toBe("http://host.docker.internal:11434");
  });

  it("prefers the probed URL over the server default", async () => {
    mockProbeOllama.mockResolvedValue({
      url: "http://ollama:11434", models: [], default_url: "http://host.docker.internal:11434"
    });
    await renderProvider("ollama");
    await waitFor(() => expect(urlInput().value).toBe("http://ollama:11434"));
  });

  it("prefills the server default in external mode", async () => {
    mockProbeOllama.mockResolvedValue({
      url: "http://ollama:11434", models: [], default_url: "http://host.docker.internal:11434"
    });
    await renderProvider("anthropic");
    fireEvent.click(screen.getByText("setup.step.provider.ollamaPrivacy.external"));
    await waitFor(() => expect(urlInput().value).toBe("http://host.docker.internal:11434"));
    expect((await patchedPayload()).ollama_base_url).toBe("http://host.docker.internal:11434");
  });

  it.each(["ollama", "external"])(
    "leaves the field empty and writes no URL when neither value exists (%s)",
    async (mode) => {
      await renderProvider(mode === "ollama" ? "ollama" : "anthropic");
      if (mode === "external") {
        fireEvent.click(screen.getByText("setup.step.provider.ollamaPrivacy.external"));
      }
      await waitFor(() => expect(mockProbeOllama).toHaveBeenCalled());
      await act(async () => {});
      expect(urlInput().value).toBe("");
      const payload = await patchedPayload();
      expect(payload).not.toHaveProperty("ollama_base_url");
      expect(JSON.stringify(mockPatch.mock.calls)).not.toContain("localhost");
    }
  );
});

describe("provider step save payload and connection test", () => {
  const renderProvider = async (initialProvider: string) => {
    render(
      <SetupWizard
        startPhase="needs_application_setup"
        initialProvider={initialProvider}
        initialModel="m"
        version="1"
        onComplete={jest.fn()}
      />
    );
    await passSetupToken("setup.step.provider.testAndContinue");
  };
  const clickTest = () => fireEvent.click(screen.getByText("setup.step.provider.testAndContinue"));
  const savedPayload = () =>
    mockPatch.mock.calls.find((c) => c[0] === "/api/settings")?.[1] as Record<string, string> | undefined;
  const failingTest = (url: string, data: Record<string, unknown>) =>
    mockPost.mockImplementation((u: string) =>
      u === url ? Promise.resolve({ data }) : Promise.resolve({ data: { ok: true } })
    );

  it("saves a cloud provider with the Ollama layers off and the key in its own field", async () => {
    await renderProvider("anthropic");
    fireEvent.change(screen.getByPlaceholderText("sk-ant-..."), {
      target: { value: "  sk-key  " }
    });

    clickTest();

    await waitFor(() => expect(savedPayload()).toBeDefined());
    expect(savedPayload()).toEqual({
      llm_provider: "anthropic",
      llm_model: "m",
      whisper_model: "base",
      use_ollama_validation_layer: "false",
      use_ollama_layer: "false",
      anthropic_api_key: "sk-key"
    });
  });

  it("points a cloud provider at the Ollama compose service and keeps the deanon model", async () => {
    await renderProvider("anthropic");
    fireEvent.click(screen.getByText("setup.step.provider.ollamaPrivacy.compose"));

    clickTest();

    await waitFor(() => expect(savedPayload()).toBeDefined());
    expect(savedPayload()).toEqual({
      llm_provider: "anthropic",
      llm_model: "m",
      whisper_model: "base",
      ollama_base_url: "http://ollama:11434",
      ollama_deanon_model: "llama3.2:3b"
    });
  });

  it("saves a local provider with the trimmed URL and the chat model", async () => {
    await renderProvider("ollama");
    fireEvent.change(screen.getByPlaceholderText("settings.llm.ollama.baseUrl.placeholder"), {
      target: { value: "  http://ollama.lan:11434  " }
    });

    clickTest();

    await waitFor(() => expect(savedPayload()).toBeDefined());
    expect(savedPayload()).toEqual({
      llm_provider: "ollama",
      llm_model: "m",
      whisper_model: "base",
      ollama_base_url: "http://ollama.lan:11434",
      ollama_deanon_model: "llama3.2:3b",
      ollama_chat_model: "m"
    });
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/settings/test-local-models", {
        base_url: "http://ollama.lan:11434"
      })
    );
  });

  it("tests a cloud provider and advances with the built-in regulations preselected", async () => {
    await renderProvider("anthropic");

    clickTest();

    expect(await screen.findByText("setup.step.regulations")).toBeInTheDocument();
    expect(mockPost).toHaveBeenCalledWith("/api/settings/test-llm", { provider: "anthropic", model: "m" });
    expect(screen.getByText("setup.nav.next")).toBeEnabled();
  });

  it("stays on the provider step and shows the server message when the test fails", async () => {
    failingTest("/api/settings/test-llm", { ok: false, message: "Invalid API key" });
    await renderProvider("anthropic");

    clickTest();

    expect(await screen.findByText("Invalid API key")).toBeInTheDocument();
    expect(screen.getByText("setup.step.provider.testAndContinue")).toBeInTheDocument();
    expect(screen.queryByText("setup.step.test.pullButton")).not.toBeInTheDocument();
  });

  it("falls back to the generic failure text when a failed test carries no message", async () => {
    failingTest("/api/settings/test-llm", { ok: false });
    await renderProvider("anthropic");

    clickTest();

    expect(await screen.findByText("setup.step.test.failed")).toBeInTheDocument();
  });

  it("offers a model pull when a local test reports a missing model", async () => {
    failingTest("/api/settings/test-local-models", { ok: false, message: "model not installed locally" });
    await renderProvider("ollama");

    clickTest();

    expect(await screen.findByText("setup.step.test.pullButton")).toBeInTheDocument();
  });

  it("does not offer a model pull for a cloud provider whose message says not installed", async () => {
    failingTest("/api/settings/test-llm", { ok: false, message: "model not installed" });
    await renderProvider("anthropic");

    clickTest();

    expect(await screen.findByText("model not installed")).toBeInTheDocument();
    expect(screen.queryByText("setup.step.test.pullButton")).not.toBeInTheDocument();
  });

  it("shows the generic failure when the test request throws without a code", async () => {
    mockPost.mockImplementation((u: string) =>
      u === "/api/settings/test-llm"
        ? Promise.reject(new Error("socket hang up"))
        : Promise.resolve({ data: { ok: true } })
    );
    await renderProvider("anthropic");

    clickTest();

    expect(await screen.findByText("setup.step.test.failed")).toBeInTheDocument();
  });

  it("advances without preselecting anything when listing regulations fails", async () => {
    mockGet.mockImplementation(() => Promise.reject(new Error("boom")));
    await renderProvider("anthropic");

    clickTest();

    expect(await screen.findByText("setup.step.regulations")).toBeInTheDocument();
    expect(screen.getByText("setup.nav.next")).toBeDisabled();
  });
});
