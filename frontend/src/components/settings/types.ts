import type { AppSettingsResponse } from "@/lib/types";

export type SettingsResponse = AppSettingsResponse;
export type SettingsUpdatePayload = Partial<Omit<AppSettingsResponse, "id">> & {
  anthropic_api_key?: string;
  openai_api_key?: string;
  openrouter_api_key?: string;
};

export type TestStatus = {
  status: "idle" | "pending" | "success" | "error";
  message?: string;
};

export type SettingsTabProps = {
  settings: SettingsResponse;
  onChange: <K extends keyof SettingsUpdatePayload>(
    key: K,
    value: SettingsUpdatePayload[K]
  ) => Promise<void>;
  isSaving: (key: keyof SettingsUpdatePayload) => boolean;
};

export const NER_MODEL_DEFAULTS: Record<string, string> = {
  en: "dslim/bert-base-NER",
  tr: "savasy/bert-base-turkish-ner-cased",
  de: "dbmdz/bert-large-cased-finetuned-conll03-german",
  fr: "Jean-Baptiste/roberta-large-ner-english",
  es: "mrm8488/bert-spanish-cased-finetuned-ner",
  nl: "wietsedv/bert-base-dutch-cased-finetuned-ner",
  zh: "uer/roberta-base-finetuned-cluener2020-chinese",
  ar: "hatmimoha/arabic-ner-bert",
  ru: "DeepPavlov/rubert-base-cased-ner",
  pt: "malduwais/biobert-base-cased-v1.2-finetuned-ner",
  ja: "cl-tohoku/bert-base-japanese",
  fallback: "Babelscape/wikineural-multilingual-ner"
};
