import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { Sidebar } from "../Sidebar";
import type { AuthUser } from "../../../lib/types";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/language", () => ({
  useLanguage: () => ({ language: "en", setLanguage: jest.fn() })
}));

jest.mock("next/navigation", () => ({
  usePathname: () => "/chat"
}));

jest.mock("../../../lib/api", () => ({
  fetchErrorLogs: jest.fn().mockResolvedValue({ total: 0 }),
  getSetupStatus: jest.fn().mockResolvedValue({ version: "1.0.0" })
}));

const admin: AuthUser = { id: 1, email: "admin@example.com", role: "admin", is_active: true };

type ChangeListener = (event: { matches: boolean }) => void;
let mediaListeners: ChangeListener[] = [];

beforeEach(() => {
  mediaListeners = [];
  window.matchMedia = jest.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: (_: string, cb: ChangeListener) => mediaListeners.push(cb),
    removeEventListener: (_: string, cb: ChangeListener) => {
      mediaListeners = mediaListeners.filter((l) => l !== cb);
    }
  })) as unknown as typeof window.matchMedia;
  document.body.style.overflow = "";
});

async function renderSidebar() {
  const utils = render(<Sidebar currentUser={admin} />);
  await act(async () => {});
  return utils;
}

async function openDrawer() {
  const trigger = screen.getByRole("button", { name: "sidebar.openMenu" });
  fireEvent.click(trigger);
  await act(async () => {});
  return { trigger, dialog: screen.getByRole("dialog") };
}

describe("Sidebar mobile drawer", () => {
  it("opens with aria-expanded and a modal dialog", async () => {
    await renderSidebar();
    const trigger = screen.getByRole("button", { name: "sidebar.openMenu" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).toBeNull();

    const { dialog } = await openDrawer();
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(trigger).toHaveAttribute("aria-controls", dialog.id);
  });

  it("moves focus into the drawer", async () => {
    await renderSidebar();
    const { dialog } = await openDrawer();
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("wraps Tab and Shift+Tab inside the drawer", async () => {
    await renderSidebar();
    const { dialog } = await openDrawer();
    const focusables = Array.from(
      dialog.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), select:not([disabled])")
    );
    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(first);

    first.focus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  it("closes on Escape and refocuses the trigger", async () => {
    await renderSidebar();
    const { trigger } = await openDrawer();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("closes when the backdrop is clicked", async () => {
    await renderSidebar();
    const { dialog } = await openDrawer();
    const backdrop = dialog.previousElementSibling as HTMLElement;
    fireEvent.click(backdrop);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes when a nav link is clicked", async () => {
    await renderSidebar();
    const { dialog } = await openDrawer();
    const link = within(dialog).getByRole("link", { name: "sidebar.chat" });
    link.addEventListener("click", (e) => e.preventDefault());
    fireEvent.click(link);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("locks body scroll while open and restores the previous value", async () => {
    document.body.style.overflow = "scroll";
    await renderSidebar();
    await openDrawer();
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.body.style.overflow).toBe("scroll");
  });

  it("closes when the viewport switches to desktop", async () => {
    await renderSidebar();
    await openDrawer();
    expect(mediaListeners.length).toBeGreaterThan(0);
    act(() => {
      mediaListeners.forEach((l) => l({ matches: false }));
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    act(() => {
      mediaListeners.forEach((l) => l({ matches: true }));
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps the responsive visibility contract", async () => {
    await renderSidebar();
    const { dialog } = await openDrawer();
    expect(dialog.parentElement).toHaveClass("lg:hidden");

    const desktopNav = screen
      .getAllByRole("navigation", { hidden: true })
      .find((nav) => !dialog.contains(nav)) as HTMLElement;
    const desktopWrapper = desktopNav.parentElement as HTMLElement;
    expect(desktopWrapper).toHaveClass("hidden");
    expect(desktopWrapper).toHaveClass("lg:flex");
  });
});
