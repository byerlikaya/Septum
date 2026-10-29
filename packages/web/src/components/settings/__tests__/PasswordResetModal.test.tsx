import { fireEvent, render, screen } from "@testing-library/react";
import { PasswordResetModal } from "../PasswordResetModal";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  PASSWORD_MIN_LENGTH: 8,
  PASSWORD_MAX_BYTES: 72
}));

function passwordInputs(): HTMLInputElement[] {
  return Array.from(document.querySelectorAll<HTMLInputElement>("input[type=password]"));
}

it("clears typed values and the local error when reopened", () => {
  const props = { email: "a@b.c", onClose: jest.fn(), onSubmit: jest.fn() };
  const { rerender } = render(<PasswordResetModal open {...props} />);

  const [pw, confirm] = passwordInputs();
  fireEvent.change(pw, { target: { value: "long-enough-1" } });
  fireEvent.change(confirm, { target: { value: "long-enough-2" } });
  fireEvent.submit(screen.getByRole("dialog").querySelector("form") as HTMLFormElement);
  expect(screen.getByText("auth.changePassword.mismatch")).toBeInTheDocument();
  expect(props.onSubmit).not.toHaveBeenCalled();

  rerender(<PasswordResetModal open={false} {...props} />);
  rerender(<PasswordResetModal open {...props} />);

  passwordInputs().forEach((input) => expect(input.value).toBe(""));
  expect(screen.queryByText("auth.changePassword.mismatch")).toBeNull();
});

function submitPassword(value: string, onSubmit: jest.Mock) {
  render(<PasswordResetModal open email="a@b.c" onClose={jest.fn()} onSubmit={onSubmit} />);
  const [pw, confirm] = passwordInputs();
  fireEvent.change(pw, { target: { value } });
  fireEvent.change(confirm, { target: { value } });
  fireEvent.submit(screen.getByRole("dialog").querySelector("form") as HTMLFormElement);
}

it("rejects a password over 72 UTF-8 bytes even when it is under 72 characters", () => {
  const onSubmit = jest.fn();
  submitPassword("ş".repeat(37), onSubmit);
  expect(screen.getByText("auth.changePassword.tooLong")).toBeInTheDocument();
  expect(onSubmit).not.toHaveBeenCalled();
});

it("accepts a password of exactly 72 ASCII bytes", () => {
  const onSubmit = jest.fn().mockResolvedValue(undefined);
  submitPassword("a".repeat(72), onSubmit);
  expect(screen.queryByText("auth.changePassword.tooLong")).toBeNull();
  expect(onSubmit).toHaveBeenCalledWith("a".repeat(72));
});
