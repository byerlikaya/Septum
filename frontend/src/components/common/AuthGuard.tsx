"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { authMe, getAuthToken, clearAuthToken } from "@/lib/api";

interface AuthGuardProps {
  children: React.ReactNode;
}

const PUBLIC_PATHS = ["/login", "/register"];

export function AuthGuard({ children }: AuthGuardProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [status, setStatus] = useState<"loading" | "authenticated" | "unauthenticated">("loading");

  const isPublicPage = PUBLIC_PATHS.some((p) => pathname.startsWith(p));

  useEffect(() => {
    if (isPublicPage) {
      setStatus("authenticated");
      return;
    }

    const token = getAuthToken();
    if (!token) {
      setStatus("unauthenticated");
      return;
    }

    let cancelled = false;
    authMe()
      .then(() => {
        if (!cancelled) setStatus("authenticated");
      })
      .catch(() => {
        if (!cancelled) {
          clearAuthToken();
          setStatus("unauthenticated");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isPublicPage]);

  if (status === "loading") return null;

  if (status === "unauthenticated" && !isPublicPage) {
    router.replace("/login");
    return null;
  }

  return <>{children}</>;
}
