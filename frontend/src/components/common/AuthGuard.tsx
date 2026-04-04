"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { authMe, getAuthToken } from "@/lib/api";

interface AuthGuardProps {
  children: React.ReactNode;
}

const PUBLIC_PATHS = ["/login", "/register"];

export function AuthGuard({ children }: AuthGuardProps) {
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    const token = getAuthToken();
    if (!token) {
      setAuthenticated(false);
      setChecked(true);
      return;
    }
    let cancelled = false;
    authMe()
      .then(() => {
        if (!cancelled) setAuthenticated(true);
      })
      .catch(() => {
        if (!cancelled) setAuthenticated(false);
      })
      .finally(() => {
        if (!cancelled) setChecked(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!checked) return null;

  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return <>{children}</>;
  }

  if (!authenticated) {
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    return null;
  }

  return <>{children}</>;
}
