import type { Document } from "@/lib/types";
import { api } from "@/lib/api";
import { getDocumentDisplayName } from "@/lib/utils";

export interface UploadDocumentsOptions {
  files: File[];
  existingDocuments: Document[];
  onProgress?: (completed: number, total: number, doc: Document) => void;
}

export interface UploadDocumentsResult {
  uploaded: Document[];
  uniqueFiles: File[];
  duplicateFiles: File[];
}

export async function uploadDocuments(
  options: UploadDocumentsOptions
): Promise<UploadDocumentsResult> {
  const { files, existingDocuments, onProgress } = options;

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
      }
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

