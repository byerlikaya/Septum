import { render, screen, waitFor } from "@testing-library/react";
import { AppShell } from "../AppShell";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/language", () => ({
  useLanguage: () => ({ language: "en", setLanguage: jest.fn() })
}));

jest.mock("next/navigation", () => ({
  usePathname: () => "/chat",
  useRouter: () => ({ replace: jest.fn() })
}));

jest.mock("../../../lib/navigation", () => ({ hardNavigate: jest.fn() }));

jest.mock("../../../components/common/ChangePasswordModal", () => ({
  ChangePasswordModal: () => null
}));

jest.mock("../../../lib/api", () => ({
  authMe: jest.fn().mockResolvedValue({ id: 1, email: "a@example.com", role: "admin", is_active: true }),
  getAuthToken: jest.fn(() => "token"),
  clearAuthToken: jest.fn(),
  fetchErrorLogs: jest.fn().mockResolvedValue({ total: 0 }),
  getSetupStatus: jest.fn().mockResolvedValue({ version: "1.0.0" })
}));

describe("AppShell", () => {
  async function renderShell() {
    render(
      <AppShell>
        <div>content</div>
      </AppShell>
    );
    await screen.findByText("content");
  }

  it("renders the sidebar", async () => {
    await renderShell();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "sidebar.openMenu" })).toBeInTheDocument()
    );
    expect(screen.getAllByRole("navigation", { hidden: true }).length).toBeGreaterThan(0);
  });

  it("labels the change-password button with the translated text", async () => {
    await renderShell();
    expect(screen.getByRole("button", { name: "sidebar.changePassword" })).toHaveAttribute(
      "aria-label",
      "sidebar.changePassword"
    );
  });

  it("takes the Swagger and API Docs labels from i18n", async () => {
    await renderShell();
    expect(screen.getByRole("link", { name: "sidebar.swagger" })).toHaveAttribute("href", "/docs");
    expect(screen.getByRole("link", { name: "sidebar.apiDocs" })).toHaveAttribute("href", "/redoc");
  });
});
