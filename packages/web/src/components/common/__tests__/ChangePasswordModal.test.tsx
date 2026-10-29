import { fireEvent, render, screen } from "@testing-library/react";
import { ChangePasswordModal } from "../ChangePasswordModal";
import { authChangePassword } from "../../../lib/api";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  PASSWORD_MIN_LENGTH: 8,
  PASSWORD_MAX_BYTES: 72,
  authChangePassword: jest.fn(),
  extractErrorDetail: jest.fn(),
  setAuthToken: jest.fn()
}));

function passwordInputs(): HTMLInputElement[] {
  return Array.from(document.querySelectorAll<HTMLInputElement>("input[type=password]"));
}

it("clears typed values and the validation error when reopened", () => {
  const onClose = jest.fn();
  const { rerender } = render(<ChangePasswordModal open onClose={onClose} />);

  const [current, next, confirm] = passwordInputs();
  fireEvent.change(current, { target: { value: "old-password" } });
  fireEvent.change(next, { target: { value: "new-password-1" } });
  fireEvent.change(confirm, { target: { value: "different-pw" } });
  fireEvent.submit(screen.getByRole("dialog").querySelector("form") as HTMLFormElement);
  expect(screen.getByText("auth.changePassword.mismatch")).toBeInTheDocument();

  rerender(<ChangePasswordModal open={false} onClose={onClose} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  rerender(<ChangePasswordModal open onClose={onClose} />);

  passwordInputs().forEach((input) => expect(input.value).toBe(""));
  expect(screen.queryByText("auth.changePassword.mismatch")).toBeNull();
});

function submitNewPassword(value: string) {
  render(<ChangePasswordModal open onClose={jest.fn()} />);
  const [current, next, confirm] = passwordInputs();
  fireEvent.change(current, { target: { value: "old-password" } });
  fireEvent.change(next, { target: { value: value } });
  fireEvent.change(confirm, { target: { value: value } });
  fireEvent.submit(screen.getByRole("dialog").querySelector("form") as HTMLFormElement);
}

it("rejects a password over 72 UTF-8 bytes even when it is under 72 characters", () => {
  submitNewPassword("ş".repeat(37));
  expect(screen.getByText("auth.changePassword.tooLong")).toBeInTheDocument();
  expect(authChangePassword).not.toHaveBeenCalled();
});

it("accepts a password of exactly 72 ASCII bytes", () => {
  (authChangePassword as jest.Mock).mockResolvedValue({ access_token: "t" });
  submitNewPassword("a".repeat(72));
  expect(screen.queryByText("auth.changePassword.tooLong")).toBeNull();
  expect(authChangePassword).toHaveBeenCalledTimes(1);
});

function rejectChangeWith(status: number, data: unknown = {}, headers: Record<string, unknown> = {}) {
  (authChangePassword as jest.Mock).mockRejectedValueOnce({
    response: { status, data, headers },
  });
}

it("shows the busy message on 503 server_busy and not the raw detail", async () => {
  rejectChangeWith(503, { detail: "Server is busy, try again shortly.", code: "server_busy" }, { "retry-after": "2" });
  submitNewPassword("a".repeat(72));
  const alert = await screen.findByText("auth.error.serverBusy");
  expect(alert).toBeInTheDocument();
  expect(screen.queryByText("Server is busy, try again shortly.")).toBeNull();
});

it("shows the rate-limit message on 429 and not the raw detail", async () => {
  rejectChangeWith(429, { detail: "Too many failed attempts." }, { "retry-after": "30" });
  submitNewPassword("a".repeat(72));
  expect(await screen.findByText("auth.error.rateLimited")).toBeInTheDocument();
  expect(screen.queryByText("Too many failed attempts.")).toBeNull();
});
