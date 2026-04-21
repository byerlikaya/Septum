"use client";

import { useI18n } from "@/lib/i18n";

interface JsonOutputPanelProps {
  content: string;
  visible: boolean;
}

/** Try to extract a JSON object from text that may be wrapped in markdown or code blocks. */
function extractJson(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  // Already valid JSON (starts with { or [)
  if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
    return trimmed;
  }
  // ```json ... ``` or ``` ... ```
  const codeBlockMatch = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (codeBlockMatch) {
    return codeBlockMatch[1].trim();
  }
  // First { to last }
  const firstBrace = trimmed.indexOf("{");
  const lastBrace = trimmed.lastIndexOf("}");
  if (firstBrace !== -1 && lastBrace > firstBrace) {
    return trimmed.slice(firstBrace, lastBrace + 1);
  }
  return null;
}

/** Build a simple JSON object from markdown-style summary (headings + list items) when no JSON found. */
function markdownToStructured(content: string): { summary: string; key_points: string[] } {
  const lines = content.split(/\n/).map((l) => l.trim()).filter(Boolean);
  const keyPoints: string[] = [];
  let summary = "";
  for (const line of lines) {
    const bulletMatch = line.match(/^[-*]\s+(.+)$/);
    const numMatch = line.match(/^\d+\.\s+(.+)$/);
    if (bulletMatch) keyPoints.push(bulletMatch[1].replace(/\*\*(.+?)\*\*/g, "$1"));
    else if (numMatch) keyPoints.push(numMatch[1].replace(/\*\*(.+?)\*\*/g, "$1"));
    else if (!line.startsWith("#") && line.length > 20) summary = summary ? `${summary} ${line}` : line;
  }
  if (!summary) summary = lines.find((l) => !l.startsWith("#") && !l.startsWith("-") && !/^\d+\./.test(l)) ?? "";
  return { summary: summary || content.slice(0, 300), key_points: keyPoints };
}

export function JsonOutputPanel({ content, visible }: JsonOutputPanelProps) {
  const t = useI18n();
  if (!visible) return <></>;

  let parsed: unknown = null;
  let parseError: string | null = null;
  let fallbackStructured: { summary: string; key_points: string[] } | null = null;
  if (content.trim()) {
    const toParse = extractJson(content);
    if (toParse) {
      try {
        parsed = JSON.parse(toParse);
      } catch (e) {
        parseError = e instanceof Error ? e.message : t("chat.json.invalid");
      }
    } else {
      parseError = t("chat.json.notFound");
      fallbackStructured = markdownToStructured(content);
    }
  }

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3 font-mono text-xs">
      <div className="mb-2 text-slate-400">
        {t("chat.json.title")}
      </div>
      {parseError ? (
        <div className="space-y-2">
          <p className="text-amber-400">{parseError}</p>
          {fallbackStructured != null && (
            <div className="rounded border border-slate-600 bg-slate-800/40 p-2">
              <p className="mb-1 text-slate-400">
                {t("chat.json.structuredTitle")}
              </p>
              <pre className="overflow-x-auto whitespace-pre-wrap break-all text-slate-300">
                {JSON.stringify(fallbackStructured, null, 2)}
              </pre>
            </div>
          )}
          <details className="mt-2">
            <summary className="cursor-pointer text-slate-500 hover:text-slate-400">
              {t("chat.json.rawTitle")}
            </summary>
            <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-all rounded border border-slate-600 bg-slate-800/60 p-2 text-slate-300">
              {content}
            </pre>
          </details>
        </div>
      ) : parsed !== null ? (
        <pre className="overflow-x-auto whitespace-pre-wrap break-all text-slate-300">
          {JSON.stringify(parsed, null, 2)}
        </pre>
      ) : (
        <p className="text-slate-500">{t("chat.json.empty")}</p>
      )}
    </div>
  );
}
