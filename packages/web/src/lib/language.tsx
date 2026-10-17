"use client";

import {
  createContext,
  useContext,
  useMemo,
  useSyncExternalStore,
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

const languageListeners = new Set<() => void>();
let currentLanguage: AppLanguage | null = null;

function readLanguage(): AppLanguage {
  if (currentLanguage === null) {
    currentLanguage = "en";
    try {
      const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
      if (stored === "tr" || stored === "en") {
        currentLanguage = stored;
      }
    } catch {
      // ignore storage errors
    }
  }
  return currentLanguage;
}

function subscribeToLanguage(listener: () => void): () => void {
  languageListeners.add(listener);
  return () => {
    languageListeners.delete(listener);
  };
}

function getServerLanguage(): AppLanguage {
  return "en";
}

function writeLanguage(next: AppLanguage): void {
  currentLanguage = next;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, next);
    } catch {
      // ignore storage errors
    }
  }
  languageListeners.forEach((listener) => listener());
}

export function LanguageProvider(props: LanguageProviderProps) {
  const { children } = props;
  const language = useSyncExternalStore(
    subscribeToLanguage,
    readLanguage,
    getServerLanguage
  );

  const value = useMemo<LanguageContextValue>(
    () => ({
      language,
      setLanguage: writeLanguage
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

