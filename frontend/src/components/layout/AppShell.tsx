"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { LogOut, FileCode, BookOpen } from "lucide-react";
import { authMe, getAuthToken, clearAuthToken } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Sidebar } from "./Sidebar";

interface AppShellProps {
  children: React.ReactNode;
}

const AUTH_PAGES = ["/login", "/register"];

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const t = useI18n();
  const [status, setStatus] = useState<"loading" | "authenticated" | "unauthenticated">("loading");

  const isAuthPage = AUTH_PAGES.some((p) => pathname.startsWith(p));

  useEffect(() => {
    if (isAuthPage) {
      setStatus("unauthenticated");
      return;
    }

    const token = getAuthToken();
    if (!token) {
      setStatus("unauthenticated");
      return;
    }

    let cancelled = false;
    authMe()
      .then(() => { if (!cancelled) setStatus("authenticated"); })
      .catch(() => {
        if (!cancelled) { clearAuthToken(); setStatus("unauthenticated"); }
      });
    return () => { cancelled = true; };
  }, [isAuthPage]);

  useEffect(() => {
    if (status === "unauthenticated" && !isAuthPage) {
      router.replace("/login");
    }
  }, [status, isAuthPage, router]);

  if (status === "loading") return null;
  if (isAuthPage) return <>{children}</>;
  if (status === "unauthenticated") return null;

  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return (
    <div className="flex h-full min-w-0 flex-col md:flex-row">
      <Sidebar />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-slate-950">
        {/* Top bar */}
        <div className="flex shrink-0 items-center justify-end gap-3 border-b border-slate-800/50 px-4 py-2 sm:px-6 lg:px-8">
          <a href={`${apiBase}/docs`} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors">
            <FileCode className="h-3.5 w-3.5" />
            <span>Swagger</span>
          </a>
          <a href={`${apiBase}/redoc`} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors">
            <BookOpen className="h-3.5 w-3.5" />
            <span>API Docs</span>
          </a>
          <div className="h-3 w-px bg-slate-700" />
          <button
            type="button"
            onClick={() => { clearAuthToken(); window.location.href = "/login"; }}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            <LogOut className="h-3.5 w-3.5" />
            <span>{t("sidebar.logout")}</span>
          </button>
        </div>
        {/* Content */}
        <main className="min-h-0 min-w-0 flex-1 overflow-hidden">
          <div className="flex h-full min-h-0 min-w-0 w-full flex-col overflow-hidden px-4 py-5 sm:px-6 lg:px-8">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
