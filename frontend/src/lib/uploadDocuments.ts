import type { Document } from "@/lib/types";
import { api } from "@/lib/api";
import { getDocumentDisplayName } from "@/lib/utils";

export interface UploadDocumentsOptions {
  files: File[];
  existingDocuments: Document[];
  onProgress?: (completed: number, total: number, doc: Document) => void;
  onUploadProgress?: (percent: number) => void;
}

export interface UploadDocumentsResult {
  uploaded: Document[];
  uniqueFiles: File[];
  duplicateFiles: File[];
}

export async function uploadDocuments(
  options: UploadDocumentsOptions
): Promise<UploadDocumentsResult> {
  const { files, existingDocuments, onProgress, onUploadProgress } = options;

  if (files.length === 0) {
    return {
      uploaded: [],
      uniqueFiles: [],
      duplicateFiles: []
    };
  }

  const existingNames = new Set(
    existingDocuments.map((doc) => getDocumentDisplayName(doc))
  );

  const uniqueFiles = files.filter((file) => !existingNames.has(file.name));
  const duplicateFiles = files.filter((file) => existingNames.has(file.name));

  const uploaded: Document[] = [];

  for (let i = 0; i < uniqueFiles.length; i++) {
    const formData = new FormData();
    formData.append("file", uniqueFiles[i]);

    const response = await api.post<Document>("/api/documents/upload", formData, {
      headers: {
        "Content-Type": "multipart/form-data"
      },
      timeout: 300_000,
      onUploadProgress: (progressEvent) => {
        if (progressEvent.total && onUploadProgress) {
          const filePct = progressEvent.loaded / progressEvent.total;
          const overall = Math.round(((i + filePct) / uniqueFiles.length) * 100);
          onUploadProgress(Math.min(overall, 99));
        }
      },
    });

    uploaded.push(response.data);
    onProgress?.(i + 1, uniqueFiles.length, response.data);
  }

  return {
    uploaded,
    uniqueFiles,
    duplicateFiles
  };
}

