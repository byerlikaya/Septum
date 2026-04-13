export function parseCommaSeparated(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

export function getDocumentDisplayName(doc: {
  original_filename?: string;
  filename: string;
}): string {
  return doc.original_filename || doc.filename;
}

