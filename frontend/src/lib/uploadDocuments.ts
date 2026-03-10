import type { Document } from "@/lib/types";
import { api } from "@/lib/api";

export interface UploadDocumentsOptions {
  files: File[];
  existingDocuments: Document[];
}

export interface UploadDocumentsResult {
  uploaded: Document[];
  uniqueFiles: File[];
  duplicateFiles: File[];
}

export async function uploadDocuments(
  options: UploadDocumentsOptions
): Promise<UploadDocumentsResult> {
  const { files, existingDocuments } = options;

  if (files.length === 0) {
    return {
      uploaded: [],
      uniqueFiles: [],
      duplicateFiles: []
    };
  }

  const existingNames = new Set(
    existingDocuments.map((doc) => doc.original_filename || doc.filename)
  );

  const uniqueFiles = files.filter((file) => !existingNames.has(file.name));
  const duplicateFiles = files.filter((file) => existingNames.has(file.name));

  const uploaded: Document[] = [];

  for (const file of uniqueFiles) {
    const formData = new FormData();
    formData.append("file", file);

    const response = await api.post<Document>("/api/documents/upload", formData, {
      headers: {
        "Content-Type": "multipart/form-data"
      }
    });

    uploaded.push(response.data);
  }

  return {
    uploaded,
    uniqueFiles,
    duplicateFiles
  };
}

