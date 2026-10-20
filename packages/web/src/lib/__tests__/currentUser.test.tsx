import { render, screen } from "@testing-library/react";
import { CurrentUserProvider, canEditDocuments, useCurrentUser } from "../currentUser";
import type { AuthUser, UserRole } from "../types";

function user(role: UserRole): AuthUser {
  return { id: 1, email: "user@example.com", role, is_active: true };
}

function RoleProbe() {
  const current = useCurrentUser();
  return <span>{current ? current.role : "none"}</span>;
}

describe("canEditDocuments", () => {
  it.each([
    ["admin", true],
    ["editor", true],
    ["viewer", false],
  ] as const)("allows %s: %s", (role, expected) => {
    expect(canEditDocuments(user(role))).toBe(expected);
  });

  it("fails closed when no user is resolved", () => {
    expect(canEditDocuments(null)).toBe(false);
  });
});

it("exposes the provided user to descendants", () => {
  render(
    <CurrentUserProvider value={user("viewer")}>
      <RoleProbe />
    </CurrentUserProvider>
  );
  expect(screen.getByText("viewer")).toBeInTheDocument();
});

it("reports no user outside a provider", () => {
  render(<RoleProbe />);
  expect(screen.getByText("none")).toBeInTheDocument();
});
