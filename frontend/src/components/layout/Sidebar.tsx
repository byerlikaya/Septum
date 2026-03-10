'use client';

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useI18n } from "@/lib/i18n";

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
  { href: "/settings/regulations", label: "Regulations" }
];

export function Sidebar(): JSX.Element {
  const pathname = usePathname();
  const t = useI18n();

  return (
    <aside className="flex h-screen w-64 flex-col border-r bg-slate-950 text-slate-50">
      <div className="flex items-center gap-2 border-b px-4 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-500 text-xs font-bold">
          S
        </div>
        <div className="flex flex-col">
          <span className="text-sm font-semibold tracking-wide">
            {t("sidebar.appName")}
          </span>
          <span className="text-xs text-slate-400">
            {t("sidebar.tagline")}
          </span>
        </div>
      </div>
      <nav className="flex-1 space-y-1 px-2 py-4">
        {navItems.map((item) => {
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
              {t(`sidebar.${item.label.toLowerCase()}` as never)}
            </Link>
          );
        })}
      </nav>
      <div className="border-t px-4 py-3 text-xs text-slate-500">
        <span>{t("sidebar.footer")}</span>
      </div>
    </aside>
  );
}

