export type IngestionStatus = "pending" | "processing" | "completed" | "failed";

export interface Document {
  id: number;
  filename: string;
  original_filename: string;
  file_type: string;
  file_format: string;
  detected_language: string;
  language_override: string | null;
  uploaded_at: string;
  encrypted_path: string;
  chunk_count: number;
  entity_count: number;
  ingestion_status: IngestionStatus;
  ingestion_error: string | null;
  file_size_bytes: number;
  transcription_text: string | null;
  ocr_confidence: number | null;
  active_regulation_ids: string[];
}

export interface DocumentListResponse {
  items: Document[];
}

export interface Chunk {
  id: number;
  document_id: number;
  index: number;
  sanitized_text: string;
  char_count: number;
  source_page: number | null;
  source_slide: number | null;
  source_sheet: number | null;
  source_timestamp_start: number | null;
  source_timestamp_end: number | null;
  section_title: string | null;
}

