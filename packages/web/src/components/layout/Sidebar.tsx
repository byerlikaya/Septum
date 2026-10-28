'use client';

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Menu, X } from "lucide-react";
import { useEscapeToClose } from "@/hooks/useEscapeToClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { fetchErrorLogs, getSetupStatus } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useLanguage } from "@/lib/language";
import { ROLE_LABEL_KEYS, type AuthUser, type UserRole } from "@/lib/types";

type NavItemDef = {
  href: string;
  label: string;
  exact?: boolean;
  allowedRoles: readonly UserRole[];
};

const ALL_ROLES: readonly UserRole[] = ["admin", "editor", "viewer"];
const ADMIN_EDITOR: readonly UserRole[] = ["admin", "editor"];
const ADMIN_ONLY: readonly UserRole[] = ["admin"];

const navItems: NavItemDef[] = [
  { href: "/chat", label: "Chat", exact: true, allowedRoles: ALL_ROLES },
  { href: "/documents", label: "Documents", allowedRoles: ADMIN_EDITOR },
  { href: "/relationships", label: "Relationships", allowedRoles: ADMIN_EDITOR },
  { href: "/settings", label: "Settings", exact: true, allowedRoles: ADMIN_ONLY },
  { href: "/settings/regulations", label: "Regulations", allowedRoles: ADMIN_ONLY },
  { href: "/settings/users", label: "Users", allowedRoles: ADMIN_ONLY },
  { href: "/settings/audit", label: "Audit Trail", exact: true, allowedRoles: ADMIN_ONLY },
  { href: "/settings/error-logs", label: "Error Logs", exact: true, allowedRoles: ADMIN_ONLY }
];

const ERROR_LOGS_HREF = "/settings/error-logs";

/** Matches Tailwind's ``lg`` breakpoint, where the drawer gives way to the fixed sidebar. */
const DESKTOP_MEDIA_QUERY = "(min-width: 1024px)";

function NavLink({
  item,
  pathname,
  errorLogCount,
  onClick
}: Readonly<{
  item: NavItemDef;
  pathname: string;
  errorLogCount: number;
  onClick?: () => void;
}>) {
  const t = useI18n();

  const activeHref = item.href.endsWith("/") ? item.href : `${item.href}/`;
  const isActive = item.exact
    ? pathname === item.href
    : pathname === item.href || pathname?.startsWith(activeHref);

  const label = t(`sidebar.${item.label.toLowerCase()}` as never);

  const content =
    item.href === ERROR_LOGS_HREF && errorLogCount > 0 ? (
      <span className="flex w-full items-center justify-between gap-2">
        <span>{label}</span>
        <span
          className="min-w-5 rounded-full bg-red-600 px-2 py-0.5 text-center text-xs font-medium text-white"
          aria-label={t("errorLogs.badgeAriaLabel").replace("{count}", String(errorLogCount))}
        >
          {errorLogCount > 99 ? "99+" : errorLogCount}
        </span>
      </span>
    ) : (
      label
    );

  return (
    <Link
      href={item.href}
      aria-current={isActive ? "page" : undefined}
      className={`flex min-h-11 items-center rounded-md px-3 py-2 text-sm font-medium lg:min-h-0 transition-colors ${
        isActive
          ? "bg-slate-800 text-slate-50"
          : "text-slate-300 hover:bg-slate-900 hover:text-slate-50"
      }`}
      onClick={onClick}
    >
      {content}
    </Link>
  );
}

function LanguageSelector() {
  const t = useI18n();
  const { language, setLanguage } = useLanguage();

  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      <span className="text-[11px] text-slate-400">
        {t("language.label")}
      </span>
      <select
        aria-label={t("language.label")}
        className="min-h-11 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] text-slate-100 lg:min-h-0"
        value={language}
        onChange={event =>
          setLanguage(event.target.value === "tr" ? "tr" : "en")
        }
      >
        <option value="en">{t("language.english")}</option>
        <option value="tr">{t("language.turkish")}</option>
      </select>
    </div>
  );
}

interface SidebarProps {
  currentUser: AuthUser | null;
}

export function Sidebar({ currentUser }: Readonly<SidebarProps>) {
  const pathname = usePathname();
  const t = useI18n();
  const [isNavOpen, setIsNavOpen] = useState<boolean>(false);
  const [errorLogCount, setErrorLogCount] = useState<number>(0);
  const drawerRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const drawerId = useId();

  const visibleNavItems = navItems.filter((item) =>
    currentUser ? item.allowedRoles.includes(currentUser.role) : false
  );

  const refreshErrorLogCount = () => {
    fetchErrorLogs({ page: 1, page_size: 1 })
      .then(res => setErrorLogCount(res.total))
      .catch(() => setErrorLogCount(0));
  };

  useEffect(() => {
    let cancelled = false;
    fetchErrorLogs({ page: 1, page_size: 1 })
      .then(res => {
        if (!cancelled) setErrorLogCount(res.total);
      })
      .catch(() => {
        if (!cancelled) setErrorLogCount(0);
      });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  useEffect(() => {
    const handler = () => refreshErrorLogCount();
    window.addEventListener("error-logs-cleared", handler);
    return () => window.removeEventListener("error-logs-cleared", handler);
  }, []);

  const [appVersion, setAppVersion] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    getSetupStatus()
      .then((status) => {
        if (!cancelled) setAppVersion(status.version);
      })
      .catch(() => {
        console.warn("Could not load the application version.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const openNav = (): void => {
    // Safari does not focus a clicked button; focus it explicitly so the
    // focus trap returns focus here when the drawer closes.
    menuButtonRef.current?.focus();
    setIsNavOpen(true);
  };
  const closeNav = useCallback((): void => setIsNavOpen(false), []);

  useEscapeToClose(isNavOpen, closeNav);
  useFocusTrap(drawerRef, isNavOpen);

  useEffect(() => {
    if (!isNavOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isNavOpen]);

  useEffect(() => {
    if (!isNavOpen || typeof window.matchMedia !== "function") return;
    const desktopQuery = window.matchMedia(DESKTOP_MEDIA_QUERY);
    const handleChange = (event: MediaQueryListEvent): void => {
      if (event.matches) setIsNavOpen(false);
    };
    desktopQuery.addEventListener("change", handleChange);
    return () => desktopQuery.removeEventListener("change", handleChange);
  }, [isNavOpen]);

  const brand = (
    <div className="flex items-center gap-2">
      <span className="text-[11px] font-medium uppercase tracking-[0.2em] text-slate-400">
        {t("sidebar.tagline")}
      </span>
      {appVersion && (
        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-500">
          v{appVersion}
        </span>
      )}
    </div>
  );

  const userInfo = currentUser && (
    <div className="border-t border-slate-800 px-4 py-2 text-[11px] text-slate-500">
      <div className="truncate text-slate-300">{currentUser.email}</div>
      <div>{t(ROLE_LABEL_KEYS[currentUser.role])}</div>
    </div>
  );

  const footer = (
    <div className="border-t border-slate-800 px-4 py-3 text-xs text-slate-500 space-y-2">
      <LanguageSelector />
      <span>{t("sidebar.footer")}</span>
    </div>
  );

  return (
    <aside className="flex w-full shrink-0 flex-col border-b border-slate-800 bg-slate-950 text-slate-50 lg:h-full lg:w-72 lg:border-b-0 lg:border-r">
      {/* Mobile / tablet bar */}
      <div className="flex items-center justify-between gap-2 px-4 py-2 lg:hidden">
        <div className="flex min-w-0 items-center gap-2">
          <Image
            src="/septum_logo.png"
            alt={t("sidebar.appName")}
            width={112}
            height={112}
            className="h-14 w-14 shrink-0"
            priority
          />
          <span className="truncate text-[11px] font-medium uppercase tracking-[0.2em] text-slate-400">
            {t("sidebar.tagline")}
          </span>
        </div>
        <button
          ref={menuButtonRef}
          type="button"
          onClick={openNav}
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md border border-slate-700 bg-slate-900 text-slate-100 transition-colors hover:bg-slate-800 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-sky-500"
          aria-label={t("sidebar.openMenu")}
          aria-expanded={isNavOpen}
          aria-controls={drawerId}
          aria-haspopup="dialog"
        >
          <Menu className="h-5 w-5" aria-hidden="true" />
        </button>
      </div>

      {isNavOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            tabIndex={-1}
            aria-hidden="true"
            onClick={closeNav}
            className="absolute inset-0 cursor-default bg-slate-950/80"
          />
          <div
            ref={drawerRef}
            id={drawerId}
            role="dialog"
            aria-modal="true"
            aria-label={t("sidebar.navigation")}
            className="relative flex h-full w-72 max-w-[85vw] flex-col overflow-y-auto border-r border-slate-800 bg-slate-950 shadow-xl"
          >
            <div className="flex items-start justify-between gap-2 border-b border-slate-800 px-4 py-3">
              <div className="flex min-w-0 flex-col gap-1">
                <Image
                  src="/septum_logo.png"
                  alt={t("sidebar.appName")}
                  width={160}
                  height={160}
                  className="h-20 w-20"
                />
                {brand}
              </div>
              <button
                type="button"
                onClick={closeNav}
                className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-50 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-sky-500"
                aria-label={t("sidebar.closeMenu")}
              >
                <X className="h-5 w-5" aria-hidden="true" />
              </button>
            </div>
            <nav aria-label={t("sidebar.navigation")} className="flex-1 space-y-1 px-2 py-4">
              {visibleNavItems.map(item => (
                <NavLink
                  key={item.href}
                  item={item}
                  pathname={pathname}
                  errorLogCount={errorLogCount}
                  onClick={closeNav}
                />
              ))}
            </nav>
            {userInfo}
            {footer}
          </div>
        </div>
      )}

      {/* Desktop layout */}
      <div className="hidden h-full flex-col lg:flex">
        <div className="flex min-h-[130px] flex-col items-center justify-center gap-3 border-b border-slate-800 px-4 py-6">
          <div className="w-full max-w-[190px]">
            <Image
              src="/septum_logo.png"
              alt={t("sidebar.appName")}
              width={380}
              height={120}
              className="h-auto w-full"
              priority
            />
          </div>
          {brand}
        </div>
        <nav aria-label={t("sidebar.navigation")} className="flex-1 space-y-1 overflow-y-auto px-2 py-4">
          {visibleNavItems.map(item => (
            <NavLink
              key={item.href}
              item={item}
              pathname={pathname}
              errorLogCount={errorLogCount}
            />
          ))}
        </nav>
        {userInfo}
        {footer}
      </div>
    </aside>
  );
}
