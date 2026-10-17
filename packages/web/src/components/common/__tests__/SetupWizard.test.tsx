import { fireEvent, render, screen } from "@testing-library/react";
import { SetupWizard } from "../SetupWizard";

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
