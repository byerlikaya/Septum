import { fireEvent, render, screen } from "@testing-library/react";
import { PasswordResetModal } from "../PasswordResetModal";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  PASSWORD_MIN_LENGTH: 8
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
  fireEvent.submit(screen.getByRole("dialog"));
  expect(screen.getByText("auth.changePassword.mismatch")).toBeInTheDocument();
  expect(props.onSubmit).not.toHaveBeenCalled();

  rerender(<PasswordResetModal open={false} {...props} />);
  rerender(<PasswordResetModal open {...props} />);

  passwordInputs().forEach((input) => expect(input.value).toBe(""));
  expect(screen.queryByText("auth.changePassword.mismatch")).toBeNull();
});
