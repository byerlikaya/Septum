'use client';

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { fetchErrorLogs } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useLanguage } from "@/lib/language";

type NavItem = {
  href: string;
  label: string;
  exact?: boolean;
};

const navItems: NavItem[] = [
  { href: "/chat", label: "Chat", exact: true },
  { href: "/documents", label: "Documents" },
  { href: "/chunks", label: "Chunks" },
  { href: "/settings", label: "Settings", exact: true },
  { href: "/settings/regulations", label: "Regulations" },
  { href: "/settings/error-logs", label: "Error Logs", exact: true }
];

const ERROR_LOGS_HREF = "/settings/error-logs";

export function Sidebar(): JSX.Element {
  const pathname = usePathname();
  const t = useI18n();
  const { language, setLanguage } = useLanguage();
  const [isNavOpen, setIsNavOpen] = useState<boolean>(false);
  const [errorLogCount, setErrorLogCount] = useState<number>(0);

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

  const toggleNav = (): void => {
    setIsNavOpen(prev => !prev);
  };

  const renderNavLabel = (item: NavItem) => {
    const label = t(`sidebar.${item.label.toLowerCase()}` as never);
    if (item.href === ERROR_LOGS_HREF && errorLogCount > 0) {
      return (
        <span className="flex w-full items-center justify-between gap-2">
          <span>{label}</span>
          <span
            className="min-w-[1.25rem] rounded-full bg-red-600 px-2 py-0.5 text-center text-xs font-medium text-white"
            aria-label={t("errorLogs.badgeAriaLabel").replace("{count}", String(errorLogCount))}
          >
            {errorLogCount > 99 ? "99+" : errorLogCount}
          </span>
        </span>
      );
    }
    return label;
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
              <span className="text-[11px] font-medium uppercase tracking-[0.2em] text-slate-400">
                {t("sidebar.tagline")}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={toggleNav}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-700 bg-slate-900 text-xs text-slate-100 transition-colors hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-0"
            aria-label="Toggle navigation"
            aria-expanded={isNavOpen}
          >
            <span className="text-base leading-none">☰</span>
          </button>
        </div>
        <div
          className={`space-y-1 px-2 pb-3 transition-[max-height] duration-200 ease-out ${
            isNavOpen ? "max-h-96" : "max-h-0 overflow-hidden"
          }`}
        >
          <div className="mt-1 flex flex-col gap-1">
            {navItems.map(item => {
              const isActive = item.exact
                ? pathname === item.href
                : pathname === item.href ||
                  pathname?.startsWith(
                    item.href.endsWith("/") ? item.href : `${item.href}/`
                  );

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                    isActive
                      ? "bg-slate-800 text-slate-50"
                      : "text-slate-300 hover:bg-slate-900 hover:text-slate-50"
                  }`}
                  onClick={() => setIsNavOpen(false)}
                >
                  {renderNavLabel(item)}
                </Link>
              );
            })}
          </div>
          <div className="mt-4 border-t border-slate-800 pt-3 text-xs text-slate-500">
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
            <span className="text-[11px] font-medium uppercase tracking-[0.26em] text-slate-400">
              {t("sidebar.tagline")}
            </span>
          </div>
        <nav className="flex-1 space-y-1 px-2 py-4">
          {navItems.map(item => {
            const isActive = item.exact
              ? pathname === item.href
              : pathname === item.href ||
                pathname?.startsWith(
                  item.href.endsWith("/") ? item.href : `${item.href}/`
                );

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-slate-800 text-slate-50"
                    : "text-slate-300 hover:bg-slate-900 hover:text-slate-50"
                }`}
              >
                {renderNavLabel(item)}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-slate-800 px-4 py-3 text-xs text-slate-500">
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
          <span>{t("sidebar.footer")}</span>
        </div>
      </div>
    </aside>
  );
}

