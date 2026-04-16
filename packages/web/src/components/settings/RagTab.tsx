import { useI18n } from "@/lib/i18n";
import type { SettingsTabProps, SettingsUpdatePayload } from "./types";
import { NumberField } from "./NumberField";

export function RagTab({
  settings,
  onChange,
  isSaving
}: SettingsTabProps) {
  const t = useI18n();
  const handleNumberBlur = async (
    key: keyof SettingsUpdatePayload,
    rawValue: string,
    fallback: number
  ): Promise<void> => {
    const value = parseInt(rawValue, 10);
    if (Number.isNaN(value)) {
      await onChange(key, fallback);
      return;
    }
    await onChange(key, value);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          {t("settings.rag.sectionTitle")}
        </h2>
        <p className="text-xs text-slate-400">
          {t("settings.rag.sectionDescription")}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <NumberField
          label={t("settings.rag.defaultChunkSize.label")}
          description={t("settings.rag.defaultChunkSize.description")}
          value={settings.chunk_size}
          onBlur={async (raw) =>
            handleNumberBlur("chunk_size", raw, settings.chunk_size)
          }
          saving={isSaving("chunk_size")}
        />

        <NumberField
          label={t("settings.rag.chunkOverlap.label")}
          description={t("settings.rag.chunkOverlap.description")}
          value={settings.chunk_overlap}
          onBlur={async (raw) =>
            handleNumberBlur("chunk_overlap", raw, settings.chunk_overlap)
          }
          saving={isSaving("chunk_overlap")}
        />

        <NumberField
          label={t("settings.rag.topK.label")}
          description={t("settings.rag.topK.description")}
          value={settings.top_k_retrieval}
          onBlur={async (raw) =>
            handleNumberBlur("top_k_retrieval", raw, settings.top_k_retrieval)
          }
          saving={isSaving("top_k_retrieval")}
        />
      </div>

      <div>
        <h3 className="mb-2 text-xs font-semibold text-slate-200">
          {t("settings.rag.formatSpecific.title")}
        </h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <NumberField
            label={t("settings.rag.pdfChunkSize.label")}
            description={t("settings.rag.pdfChunkSize.description")}
            value={settings.pdf_chunk_size}
            onBlur={async (raw) =>
              handleNumberBlur("pdf_chunk_size", raw, settings.pdf_chunk_size)
            }
            saving={isSaving("pdf_chunk_size")}
          />

          <NumberField
            label={t("settings.rag.audioChunkSize.label")}
            description={t("settings.rag.audioChunkSize.description")}
            value={settings.audio_chunk_size}
            onBlur={async (raw) =>
              handleNumberBlur("audio_chunk_size", raw, settings.audio_chunk_size)
            }
            saving={isSaving("audio_chunk_size")}
          />

          <NumberField
            label={t("settings.rag.spreadsheetChunkSize.label")}
            description={t("settings.rag.spreadsheetChunkSize.description")}
            value={settings.spreadsheet_chunk_size}
            onBlur={async (raw) =>
              handleNumberBlur(
                "spreadsheet_chunk_size",
                raw,
                settings.spreadsheet_chunk_size
              )
            }
            saving={isSaving("spreadsheet_chunk_size")}
          />
        </div>
      </div>
    </div>
  );
}
