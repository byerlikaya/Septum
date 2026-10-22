/** Base ISO 639 code: trimmed, lower-cased, region/script subtags dropped (mirrors the API's canonicalisation). */
export function baseLanguageCode(code: string): string {
  return code.trim().toLowerCase().split(/[-_]/)[0];
}
