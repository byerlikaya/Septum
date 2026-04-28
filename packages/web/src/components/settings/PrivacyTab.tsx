import { useI18n } from "@/lib/i18n";
import type { AppSettingsResponse } from "@/lib/types";
import type { SettingsTabProps } from "./types";
import { FieldHint } from "./FieldHint";
import { SavingIndicator } from "./SavingIndicator";
import { ToggleField } from "./ToggleField";

function onLocalFieldChange<K extends keyof AppSettingsResponse>(
  current: AppSettingsResponse,
  key: K,
  value: AppSettingsResponse[K] | string
): void {
  void current;
  void key;
  void value;
}

export function PrivacyTab({
  settings,
  onChange,
  isSaving
}: SettingsTabProps) {
  const t = useI18n();
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          {t("settings.privacy.sectionTitle")}
        </h2>
        <p className="text-xs text-slate-400">
          {t("settings.privacy.sectionDescription")}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <ToggleField
          label={t("settings.privacy.deanon.label")}
          description={t("settings.privacy.deanon.description")}
          checked={settings.deanon_enabled}
          onToggle={async (value) => {
            onLocalFieldChange(settings, "deanon_enabled", value);
            await onChange("deanon_enabled", value);
          }}
          saving={isSaving("deanon_enabled")}
        />

        <ToggleField
          label={t("settings.privacy.requireApproval.label")}
          description={t("settings.privacy.requireApproval.description")}
          checked={settings.require_approval}
          onToggle={async (value) => {
            onLocalFieldChange(settings, "require_approval", value);
            await onChange("require_approval", value);
          }}
          saving={isSaving("require_approval")}
        />

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            {t("settings.privacy.approvalTimeout.label")}
          </label>
          <input
            type="number"
            min={0}
            step={30}
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            value={settings.approval_timeout_seconds}
            onChange={async (event) => {
              const parsed = Number(event.target.value);
              const value = Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
              await onChange("approval_timeout_seconds", value);
            }}
          />
          <FieldHint text={t("settings.privacy.approvalTimeout.hint")} />
          {isSaving("approval_timeout_seconds") && <SavingIndicator />}
        </div>

        <ToggleField
          label={t("settings.privacy.showJson.label")}
          description={t("settings.privacy.showJson.description")}
          checked={settings.show_json_output}
          onToggle={async (value) => {
            onLocalFieldChange(settings, "show_json_output", value);
            await onChange("show_json_output", value);
          }}
          saving={isSaving("show_json_output")}
        />



      </div>

      <div>
        <h3 className="mb-2 text-xs font-semibold text-slate-200">
          {t("settings.privacy.layers.title")}
        </h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
          <ToggleField
            label={t("settings.privacy.layers.presidio.label")}
            description={t("settings.privacy.layers.presidio.description")}
            checked={settings.use_presidio_layer}
            onToggle={async (value) => {
              onLocalFieldChange(settings, "use_presidio_layer", value);
              await onChange("use_presidio_layer", value);
            }}
            saving={isSaving("use_presidio_layer")}
          />
          <ToggleField
            label={t("settings.privacy.layers.ner.label")}
            description={t("settings.privacy.layers.ner.description")}
            checked={settings.use_ner_layer}
            onToggle={async (value) => {
              onLocalFieldChange(settings, "use_ner_layer", value);
              await onChange("use_ner_layer", value);
            }}
            saving={isSaving("use_ner_layer")}
          />
          <ToggleField
            label={t("settings.privacy.layers.ollamaValidation.label")}
            description={t("settings.privacy.layers.ollamaValidation.description")}
            checked={settings.use_ollama_validation_layer}
            onToggle={async (value) => {
              onLocalFieldChange(settings, "use_ollama_validation_layer", value);
              await onChange("use_ollama_validation_layer", value);
            }}
            saving={isSaving("use_ollama_validation_layer")}
          />
          <ToggleField
            label={t("settings.privacy.layers.ollama.label")}
            description={t("settings.privacy.layers.ollama.description")}
            checked={settings.use_ollama_layer}
            onToggle={async (value) => {
              onLocalFieldChange(settings, "use_ollama_layer", value);
              await onChange("use_ollama_layer", value);
            }}
            saving={isSaving("use_ollama_layer")}
          />
          <ToggleField
            label={t("settings.privacy.layers.ollamaSemantic.label")}
            description={t("settings.privacy.layers.ollamaSemantic.description")}
            checked={settings.use_ollama_semantic_layer}
            onToggle={async (value) => {
              onLocalFieldChange(settings, "use_ollama_semantic_layer", value);
              await onChange("use_ollama_semantic_layer", value);
            }}
            saving={isSaving("use_ollama_semantic_layer")}
          />
        </div>
      </div>
    </div>
  );
}
