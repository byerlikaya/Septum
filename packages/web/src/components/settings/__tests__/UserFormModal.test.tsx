import { fireEvent, render, screen } from "@testing-library/react";
import { UserFormModal } from "../UserFormModal";
import type { UserListItem } from "../../../lib/types";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  PASSWORD_MIN_LENGTH: 8
}));

const user = {
  id: 7,
  email: "member@example.com",
  role: "viewer",
  is_active: false
} as unknown as UserListItem;

function emailInput(): HTMLInputElement {
  return document.querySelector<HTMLInputElement>("input[type=email]")!;
}

it("restores the initial user values when reopened after an abandoned edit", () => {
  const base = { mode: "edit" as const, initial: user, onClose: jest.fn(), onSubmit: jest.fn() };
  const { rerender } = render(<UserFormModal open {...base} />);
  expect(emailInput().value).toBe("member@example.com");

  fireEvent.change(emailInput(), { target: { value: "changed@example.com" } });
  expect(emailInput().value).toBe("changed@example.com");

  rerender(<UserFormModal open={false} {...base} />);
  rerender(<UserFormModal open {...base} />);
  expect(emailInput().value).toBe("member@example.com");
});

it("keeps in-flight edits when the parent re-renders while open", () => {
  const base = { mode: "edit" as const, initial: user, onClose: jest.fn(), onSubmit: jest.fn() };
  const { rerender } = render(<UserFormModal open {...base} />);
  fireEvent.change(emailInput(), { target: { value: "typing@example.com" } });
  rerender(<UserFormModal open {...base} initial={{ ...user }} />);
  expect(emailInput().value).toBe("typing@example.com");
});

it("starts blank in create mode after reopening", () => {
  const base = { mode: "create" as const, onClose: jest.fn(), onSubmit: jest.fn() };
  const { rerender } = render(<UserFormModal open {...base} />);
  fireEvent.change(emailInput(), { target: { value: "x@y.z" } });
  rerender(<UserFormModal open={false} {...base} />);
  rerender(<UserFormModal open {...base} />);
  expect(emailInput().value).toBe("");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});
