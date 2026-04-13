"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { LogOut, FileCode, BookOpen, KeyRound } from "lucide-react";
import { authMe, getAuthToken, clearAuthToken } from "@/lib/api";
import { ROLE_LABEL_KEYS, type AuthUser, type UserRole } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import { Sidebar } from "./Sidebar";
import { ChangePasswordModal } from "@/components/common/ChangePasswordModal";

interface AppShellProps {
  children: React.ReactNode;
}

const AUTH_PAGES = ["/login"];

const ALL_ROLES: readonly UserRole[] = ["admin", "editor", "viewer"];
const ADMIN_EDITOR: readonly UserRole[] = ["admin", "editor"];
const ADMIN_ONLY: readonly UserRole[] = ["admin"];

const PAGE_ROLES: { readonly prefix: string; readonly allowed: readonly UserRole[] }[] = [
  { prefix: "/chat", allowed: ALL_ROLES },
  { prefix: "/documents", allowed: ADMIN_EDITOR },
  { prefix: "/settings/users", allowed: ADMIN_ONLY },
  { prefix: "/settings/regulations", allowed: ADMIN_ONLY },
  { prefix: "/settings/audit", allowed: ADMIN_ONLY },
  { prefix: "/settings/error-logs", allowed: ADMIN_ONLY },
  { prefix: "/settings", allowed: ADMIN_ONLY },
];

function allowedRolesForPath(pathname: string): readonly UserRole[] {
  const match = PAGE_ROLES.find(
    (entry) => pathname === entry.prefix || pathname.startsWith(`${entry.prefix}/`)
  );
  return match ? match.allowed : ALL_ROLES;
}

type AuthState = "loading" | AuthUser | null;

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const t = useI18n();
  const [authState, setAuthState] = useState<AuthState>("loading");
  const [changePasswordOpen, setChangePasswordOpen] = useState<boolean>(false);

  const isAuthPage = AUTH_PAGES.some((p) => pathname.startsWith(p));

  useEffect(() => {
    if (isAuthPage) {
      setAuthState(null);
      return;
    }

    const token = getAuthToken();
    if (!token) {
      setAuthState(null);
      return;
    }

    let cancelled = false;
    authMe()
      .then((user) => { if (!cancelled) setAuthState(user); })
      .catch(() => {
        if (!cancelled) {
          clearAuthToken();
          setAuthState(null);
        }
      });
    return () => { cancelled = true; };
  }, [isAuthPage]);

  useEffect(() => {
    if (authState === null && !isAuthPage) {
      router.replace("/login");
    }
  }, [authState, isAuthPage, router]);

  useEffect(() => {
    if (authState === "loading" || authState === null || isAuthPage) return;
    const allowed = allowedRolesForPath(pathname);
    if (!allowed.includes(authState.role)) {
      router.replace("/chat");
    }
  }, [authState, pathname, isAuthPage, router]);

  if (authState === "loading") return null;
  if (isAuthPage) return <>{children}</>;
  if (authState === null) return null;

  const currentUser = authState;
  if (!allowedRolesForPath(pathname).includes(currentUser.role)) {
    return null;
  }

  return (
    <div className="flex h-full min-w-0 flex-col md:flex-row">
      <Sidebar currentUser={currentUser} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-slate-950">
        {/* Top bar */}
        <div className="flex shrink-0 items-center justify-end gap-3 border-b border-slate-800/50 px-4 py-2 sm:px-6 lg:px-8">
          <a href="/docs" target="_blank" rel="noopener noreferrer" className="hidden items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors sm:flex">
            <FileCode className="h-3.5 w-3.5" />
            <span>Swagger</span>
          </a>
          <a href="/redoc" target="_blank" rel="noopener noreferrer" className="hidden items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors sm:flex">
            <BookOpen className="h-3.5 w-3.5" />
            <span>API Docs</span>
          </a>
          <div className="hidden h-3 w-px bg-slate-700 sm:block" />
          <span className="hidden text-xs text-slate-400 sm:inline">
            {currentUser.email} · {t(ROLE_LABEL_KEYS[currentUser.role])}
          </span>
          <button
            type="button"
            onClick={() => setChangePasswordOpen(true)}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            <KeyRound className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">{t("sidebar.changePassword")}</span>
          </button>
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
        <main className="min-h-0 min-w-0 flex-1 overflow-auto md:overflow-hidden">
          <div className="flex min-h-full md:h-full min-w-0 w-full flex-col overflow-visible md:overflow-hidden px-4 py-5 sm:px-6 lg:px-8">
            {children}
          </div>
        </main>
      </div>

      {changePasswordOpen && (
        <ChangePasswordModal
          open
          onClose={() => setChangePasswordOpen(false)}
        />
      )}
    </div>
  );
}
