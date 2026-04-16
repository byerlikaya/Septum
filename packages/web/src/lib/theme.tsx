"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type AppTheme = "light" | "dark";

type ThemeContextValue = {
  theme: AppTheme;
  setTheme: (theme: AppTheme) => void;
  toggleTheme: () => void;
};

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

const THEME_STORAGE_KEY = "septum.theme";

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<AppTheme>("dark");

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
      if (stored === "light" || stored === "dark") {
        setThemeState(stored);
        document.documentElement.classList.toggle("dark", stored === "dark");
      }
    } catch {
      // ignore
    }
  }, []);

  const setTheme = (next: AppTheme): void => {
    setThemeState(next);
    document.documentElement.classList.toggle("dark", next === "dark");
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(THEME_STORAGE_KEY, next);
      } catch {
        // ignore
      }
    }
  };

  const toggleTheme = (): void => {
    setTheme(theme === "dark" ? "light" : "dark");
  };

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, toggleTheme }),
    [theme] // eslint-disable-line react-hooks/exhaustive-deps
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within a ThemeProvider.");
  }
  return ctx;
}
