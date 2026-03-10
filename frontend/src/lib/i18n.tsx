"use client";

import { useMemo } from "react";
import { useLanguage } from "@/lib/language";
import { enMessages } from "@/i18n/en";
import { trMessages } from "@/i18n/tr";

const dictionaries: Record<string, Record<string, string>> = {
  en: enMessages,
  tr: trMessages
};

export function useI18n(): (key: keyof typeof enMessages) => string {
  const { language } = useLanguage();

  const dict = useMemo(() => {
    const primary = dictionaries[language] ?? dictionaries.en;
    return primary;
  }, [language]);

  return (key: keyof typeof enMessages): string => {
    const fromPrimary = dict[key as string];
    if (typeof fromPrimary === "string" && fromPrimary.length > 0) {
      return fromPrimary;
    }
    const fromEn = (enMessages as Record<string, string>)[key as string];
    if (typeof fromEn === "string" && fromEn.length > 0) {
      return fromEn;
    }
    return key;
  };
}

