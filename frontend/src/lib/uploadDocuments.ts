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
  const total = uniqueFiles.length;

  // Track per-file upload progress so the overall bar reflects parallel uploads.
  const fileBytes = new Array<number>(total).fill(0);
  const fileTotals = new Array<number>(total).fill(0);

  const reportOverall = () => {
    if (!onUploadProgress) return;
    let loaded = 0;
    let totalBytes = 0;
    for (let i = 0; i < total; i++) {
      loaded += fileBytes[i];
      totalBytes += fileTotals[i] || uniqueFiles[i].size;
    }
    if (totalBytes > 0) {
      const pct = Math.round((loaded / totalBytes) * 100);
      onUploadProgress(Math.min(pct, 99));
    }
  };

  const uploadOne = async (index: number): Promise<Document> => {
    const formData = new FormData();
    formData.append("file", uniqueFiles[index]);

    const response = await api.post<Document>("/api/documents/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 300_000,
      onUploadProgress: (progressEvent) => {
        if (progressEvent.total) {
          fileBytes[index] = progressEvent.loaded;
          fileTotals[index] = progressEvent.total;
          reportOverall();
        }
      },
    });
    return response.data;
  };

  // Upload in parallel with a concurrency cap to avoid overwhelming the server
  // and triggering SQLite write contention on the backend.
  const CONCURRENCY = 4;
  let nextIndex = 0;
  let completedCount = 0;

  const worker = async (): Promise<void> => {
    while (true) {
      const myIndex = nextIndex++;
      if (myIndex >= total) return;
      const doc = await uploadOne(myIndex);
      uploaded[myIndex] = doc;
      completedCount += 1;
      onProgress?.(completedCount, total, doc);
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(CONCURRENCY, total) }, () => worker())
  );

  return {
    uploaded: uploaded.filter(Boolean),
    uniqueFiles,
    duplicateFiles
  };
}

