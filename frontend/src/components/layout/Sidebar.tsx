'use client';

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowUpCircle } from "lucide-react";
import api, { fetchErrorLogs } from "@/lib/api";
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
  { href: "/settings", label: "Settings", exact: true, allowedRoles: ADMIN_ONLY },
  { href: "/settings/regulations", label: "Regulations", allowedRoles: ADMIN_ONLY },
  { href: "/settings/users", label: "Users", allowedRoles: ADMIN_ONLY },
  { href: "/settings/audit", label: "Audit Trail", exact: true, allowedRoles: ADMIN_ONLY },
  { href: "/settings/error-logs", label: "Error Logs", exact: true, allowedRoles: ADMIN_ONLY }
];

const ERROR_LOGS_HREF = "/settings/error-logs";

function NavLink({
  item,
  pathname,
  errorLogCount,
  onClick
}: {
  item: NavItemDef;
  pathname: string;
  errorLogCount: number;
  onClick?: () => void;
}) {
  const t = useI18n();

  const isActive = item.exact
    ? pathname === item.href
    : pathname === item.href ||
      pathname?.startsWith(
        item.href.endsWith("/") ? item.href : `${item.href}/`
      );

  const label = t(`sidebar.${item.label.toLowerCase()}` as never);

  const content =
    item.href === ERROR_LOGS_HREF && errorLogCount > 0 ? (
      <span className="flex w-full items-center justify-between gap-2">
        <span>{label}</span>
        <span
          className="min-w-[1.25rem] rounded-full bg-red-600 px-2 py-0.5 text-center text-xs font-medium text-white"
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
      className={`flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
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
        className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] text-slate-100"
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

export function Sidebar({ currentUser }: SidebarProps) {
  const pathname = usePathname();
  const t = useI18n();
  const [isNavOpen, setIsNavOpen] = useState<boolean>(false);
  const [errorLogCount, setErrorLogCount] = useState<number>(0);

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

  // Version + update check
  const [appVersion, setAppVersion] = useState<string>("");
  const [updateInfo, setUpdateInfo] = useState<{ latest: string; command: string } | null>(null);

  useEffect(() => {
    api.get<{ update_available: boolean; current_version: string; latest_version: string; update_command: string }>("/api/setup/check-update")
      .then(({ data }) => {
        setAppVersion(data.current_version);
        if (data.update_available) setUpdateInfo({ latest: data.latest_version, command: data.update_command });
      })
      .catch(() => {});
  }, []);

  const toggleNav = (): void => {
    setIsNavOpen(prev => !prev);
  };

  return (
    <aside className="flex w-full flex-col border-b border-slate-800 bg-slate-950 text-slate-50 md:h-screen md:w-72 md:border-b-0 md:border-r">
      {/* Mobile layout */}
      <div className="md:hidden">
        <div className="flex items-center justify-between gap-2 px-4 py-5">
          <div className="flex items-center gap-3">
            <div className="flex flex-col gap-1">
              <div className="w-full max-w-[170px]">
                <Image
                  src="/septum_logo.png"
                  alt={t("sidebar.appName")}
                  width={340}
                  height={110}
                  className="h-auto w-full"
                  priority
                />
              </div>
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
            </div>
          </div>
          <button
            type="button"
            onClick={toggleNav}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-700 bg-slate-900 text-xs text-slate-100 transition-colors hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-0"
            aria-label="Toggle navigation"
            aria-expanded={isNavOpen}
          >
            <span className="text-base leading-none">&#9776;</span>
          </button>
        </div>
        <div
          className={`space-y-1 px-2 pb-3 transition-[max-height] duration-200 ease-out ${
            isNavOpen ? "max-h-96" : "max-h-0 overflow-hidden"
          }`}
        >
          <div className="mt-1 flex flex-col gap-1">
            {visibleNavItems.map(item => (
              <NavLink
                key={item.href}
                item={item}
                pathname={pathname}
                errorLogCount={errorLogCount}
                onClick={() => setIsNavOpen(false)}
              />
            ))}
          </div>
          <div className="mt-4 border-t border-slate-800 pt-3 text-xs text-slate-500 space-y-2">
            <LanguageSelector />
            <span>{t("sidebar.footer")}</span>
          </div>
        </div>
      </div>

      {/* Desktop layout */}
        <div className="hidden h-full flex-col md:flex">
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
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-medium uppercase tracking-[0.26em] text-slate-400">
                {t("sidebar.tagline")}
              </span>
              {appVersion && (
                <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-500">
                  v{appVersion}
                </span>
              )}
            </div>
          </div>
        <nav className="flex-1 space-y-1 px-2 py-4">
          {visibleNavItems.map(item => (
            <NavLink
              key={item.href}
              item={item}
              pathname={pathname}
              errorLogCount={errorLogCount}
            />
          ))}
        </nav>
        {currentUser && (
          <div className="border-t border-slate-800 px-4 py-2 text-[11px] text-slate-500">
            <div className="truncate text-slate-300">{currentUser.email}</div>
            <div>{t(ROLE_LABEL_KEYS[currentUser.role])}</div>
          </div>
        )}
        <div className="border-t border-slate-800 px-4 py-3 text-xs text-slate-500 space-y-2">
          {updateInfo && (
            <div className="rounded-md border border-sky-800 bg-sky-950/50 p-2 space-y-1">
              <div className="flex items-center gap-1.5 text-sky-300 font-medium">
                <ArrowUpCircle className="h-3.5 w-3.5" />
                <span>v{updateInfo.latest} {t("sidebar.updateAvailable")}</span>
              </div>
              <code className="block rounded bg-slate-950 px-1.5 py-1 text-[10px] text-slate-400 select-all">{updateInfo.command}</code>
            </div>
          )}
          <LanguageSelector />
          <span>{t("sidebar.footer")}</span>
        </div>
      </div>
    </aside>
  );
}
