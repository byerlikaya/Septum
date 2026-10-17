import { fireEvent, render, screen } from "@testing-library/react";
import { ChangePasswordModal } from "../ChangePasswordModal";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  PASSWORD_MIN_LENGTH: 8,
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
  fireEvent.submit(screen.getByRole("dialog"));
  expect(screen.getByText("auth.changePassword.mismatch")).toBeInTheDocument();

  rerender(<ChangePasswordModal open={false} onClose={onClose} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  rerender(<ChangePasswordModal open onClose={onClose} />);

  passwordInputs().forEach((input) => expect(input.value).toBe(""));
  expect(screen.queryByText("auth.changePassword.mismatch")).toBeNull();
});
