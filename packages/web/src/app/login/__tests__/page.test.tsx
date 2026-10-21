import { fireEvent, render, screen } from "@testing-library/react";
import LoginPage from "../page";
import { authLogin } from "../../../lib/api";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/navigation", () => ({
  hardNavigate: jest.fn()
}));

jest.mock("../../../lib/api", () => ({
  authLogin: jest.fn(),
  setAuthToken: jest.fn()
}));

function submitLogin() {
  render(<LoginPage />);
  fireEvent.change(document.querySelector<HTMLInputElement>("input[type=email]")!, {
    target: { value: "admin@example.com" }
  });
  fireEvent.change(document.querySelector<HTMLInputElement>("input[type=password]")!, {
    target: { value: "correct-horse-battery" }
  });
  fireEvent.submit(document.querySelector("form")!);
}

function rejectLoginWith(status: number, data: unknown, headers: Record<string, unknown> = {}) {
  (authLogin as jest.Mock).mockRejectedValueOnce({ response: { status, data, headers } });
}

it("shows the generic invalid-credentials message on 401", async () => {
  rejectLoginWith(401, { detail: "Invalid credentials" });
  submitLogin();
  expect(await screen.findByText("auth.login.error")).toBeInTheDocument();
});

it("shows the busy message on 503 server_busy and not the raw detail", async () => {
  rejectLoginWith(503, { detail: "Server is busy, try again shortly.", code: "server_busy" }, { "retry-after": "2" });
  submitLogin();
  expect(await screen.findByText("auth.error.serverBusy")).toBeInTheDocument();
  expect(screen.queryByText("auth.login.error")).toBeNull();
  expect(screen.queryByText("Server is busy, try again shortly.")).toBeNull();
});

it("shows the rate-limit message on 429 with Retry-After and not the raw detail", async () => {
  rejectLoginWith(429, { detail: "Too many failed attempts." }, { "retry-after": "60" });
  submitLogin();
  expect(await screen.findByText("auth.error.rateLimited")).toBeInTheDocument();
  expect(screen.queryByText("Too many failed attempts.")).toBeNull();
});

it("shows the generic rate-limit message on 429 without Retry-After", async () => {
  rejectLoginWith(429, { detail: "Too many failed attempts." });
  submitLogin();
  expect(await screen.findByText("auth.error.rateLimitedGeneric")).toBeInTheDocument();
});
