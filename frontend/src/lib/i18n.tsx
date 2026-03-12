"use client";

import { useCallback, useMemo } from "react";
import { useLanguage } from "@/lib/language";
import { enMessages } from "@/i18n/en";
import { trMessages } from "@/i18n/tr";

const dictionaries: Record<string, Record<string, string>> = {
  en: enMessages,
  tr: trMessages
};

type TranslationParams = Record<string, string | number>;

export function useI18n(): (key: keyof typeof enMessages, params?: TranslationParams) => string {
  const { language } = useLanguage();

  const dict = useMemo(() => {
    const primary = dictionaries[language] ?? dictionaries.en;
    return primary;
  }, [language]);

  const translate = useCallback(
    (key: keyof typeof enMessages, params?: TranslationParams): string => {
      const template =
        dict[key as string] ??
        (enMessages as Record<string, string>)[key as string] ??
        (key as string);

      if (!params) {
        return template;
      }

      return Object.entries(params).reduce((text, [name, value]) => {
        const token = `{${name}}`;
        return text.split(token).join(String(value));
      }, template);
    },
    [dict]
  );

  return translate;
}

