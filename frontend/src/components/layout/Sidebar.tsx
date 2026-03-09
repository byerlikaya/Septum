'use client';

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/chat", label: "Chat" },
  { href: "/documents", label: "Documents" },
  { href: "/chunks", label: "Chunks" },
  { href: "/settings", label: "Settings" }
];

export function Sidebar(): JSX.Element {
  const pathname = usePathname();

  return (
    <aside className="flex h-screen w-64 flex-col border-r bg-slate-950 text-slate-50">
      <div className="flex items-center gap-2 border-b px-4 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-500 text-xs font-bold">
          S
        </div>
        <div className="flex flex-col">
          <span className="text-sm font-semibold tracking-wide">
            Septum
          </span>
          <span className="text-xs text-slate-400">AI Privacy Gateway</span>
        </div>
      </div>
      <nav className="flex-1 space-y-1 px-2 py-4">
        {navItems.map((item) => {
          const isActive =
            pathname === item.href ||
            (item.href !== "/chat" && pathname?.startsWith(item.href));

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
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="border-t px-4 py-3 text-xs text-slate-500">
        Privacy-first · Local-first
      </div>
    </aside>
  );
}

