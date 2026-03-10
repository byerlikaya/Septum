"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode
} from "react";

export type AppLanguage = "en" | "tr";

type LanguageContextValue = {
  language: AppLanguage;
  setLanguage: (language: AppLanguage) => void;
};

const LanguageContext = createContext<LanguageContextValue | undefined>(
  undefined
);

const LANGUAGE_STORAGE_KEY = "septum.language";

type LanguageProviderProps = {
  children: ReactNode;
};

export function LanguageProvider(
  props: LanguageProviderProps
): JSX.Element {
  const { children } = props;
  const [language, setLanguageState] = useState<AppLanguage>("en");

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    try {
      const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
      if (stored === "tr" || stored === "en") {
        setLanguageState(stored);
      }
    } catch {
      // ignore storage errors
    }
  }, []);

  const setLanguage = (next: AppLanguage): void => {
    setLanguageState(next);
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(LANGUAGE_STORAGE_KEY, next);
      } catch {
        // ignore storage errors
      }
    }
  };

  const value = useMemo<LanguageContextValue>(
    () => ({
      language,
      setLanguage
    }),
    [language]
  );

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage(): LanguageContextValue {
  const ctx = useContext(LanguageContext);
  if (!ctx) {
    throw new Error("useLanguage must be used within a LanguageProvider.");
  }
  return ctx;
}

