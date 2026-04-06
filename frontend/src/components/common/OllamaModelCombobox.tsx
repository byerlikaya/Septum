"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import api from "@/lib/api";
import { useI18n } from "@/lib/i18n";

interface OllamaModel {
  name: string;
  size: string;
  parameter_size: string | null;
  quantization: string | null;
}

interface OllamaModelComboboxProps {
  value: string;
  onChange: (value: string) => void;
  baseUrl: string;
  placeholder?: string;
  className?: string;
}

/**
 * Combobox that lists locally available Ollama models and allows free-text
 * input for models that are not yet installed.
 */
export function OllamaModelCombobox({
  value,
  onChange,
  baseUrl,
  placeholder = "llama3.2:3b",
  className = "",
}: OllamaModelComboboxProps) {
  const t = useI18n();
  const [models, setModels] = useState<OllamaModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState(value);
  const [isSearching, setIsSearching] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Sync external value into filter text
  useEffect(() => {
    setFilter(value);
    setIsSearching(false);
  }, [value]);

  // Fetch models when baseUrl changes
  const fetchModels = useCallback(async () => {
    if (!baseUrl.trim()) {
      setModels([]);
      return;
    }
    setLoading(true);
    try {
      const { data } = await api.get<{ models: OllamaModel[] }>(
        "/api/settings/ollama-models",
        { params: { base_url: baseUrl } },
      );
      setModels(data.models);
    } catch {
      setModels([]);
    } finally {
      setLoading(false);
    }
  }, [baseUrl]);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const filtered = isSearching && filter
    ? models.filter((m) => m.name.toLowerCase().includes(filter.toLowerCase()))
    : models;

  const handleSelect = (name: string) => {
    setFilter(name);
    setIsSearching(false);
    onChange(name);
    setOpen(false);
  };

  const handleInputChange = (text: string) => {
    setFilter(text);
    setIsSearching(true);
    if (!open) setOpen(true);
  };

  const handleBlur = () => {
    // Commit free-text value on blur (allow custom model names)
    const trimmed = filter.trim();
    if (trimmed && trimmed !== value) {
      onChange(trimmed);
    }
  };

  const baseCls =
    "w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus-within:border-sky-500 focus-within:ring-1 focus-within:ring-sky-500";

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <div className={baseCls + " flex items-center gap-1"}>
        <input
          ref={inputRef}
          type="text"
          value={filter}
          onChange={(e) => handleInputChange(e.target.value)}
          onFocus={() => setOpen(true)}
          onBlur={handleBlur}
          placeholder={placeholder}
          className="flex-1 bg-transparent outline-none text-sm text-slate-200 placeholder-slate-500"
        />
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-slate-500" />
        ) : (
          <button
            type="button"
            tabIndex={-1}
            onMouseDown={(e) => {
              e.preventDefault();
              setOpen((o) => !o);
              inputRef.current?.focus();
            }}
            className="shrink-0 text-slate-500 hover:text-slate-300"
          >
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-md border border-slate-700 bg-slate-800 shadow-lg max-h-48 overflow-y-auto">
          {filtered.length > 0 ? (
            filtered.map((m) => (
              <button
                key={m.name}
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  handleSelect(m.name);
                }}
                className={`w-full flex items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-slate-700/60 transition-colors ${
                  m.name === value ? "bg-sky-600/15 text-sky-300" : "text-slate-200"
                }`}
              >
                <span className="truncate font-medium">{m.name}</span>
                <span className="shrink-0 flex items-center gap-1.5 text-[11px] text-slate-500">
                  {m.parameter_size && <span>{m.parameter_size}</span>}
                  <span>{m.size}</span>
                </span>
              </button>
            ))
          ) : loading ? (
            <div className="px-3 py-2 text-xs text-slate-500 flex items-center gap-2">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t("ollama.models.loading")}
            </div>
          ) : models.length === 0 ? (
            <div className="px-3 py-2 text-xs text-slate-500">
              {t("ollama.models.empty")}
            </div>
          ) : (
            <div className="px-3 py-2 text-xs text-slate-500">
              {t("ollama.models.noMatch")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
