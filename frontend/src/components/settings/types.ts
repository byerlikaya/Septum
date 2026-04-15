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

