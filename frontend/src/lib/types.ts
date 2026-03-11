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

export interface SpreadsheetColumn {
  index: number;
  technical_label: string;
  semantic_label: string | null;
  is_numeric: boolean | null;
}

export interface SpreadsheetSchema {
  document_id: number;
  columns: SpreadsheetColumn[];
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

export interface ChunkListResponse {
  items: Chunk[];
}

// --- Chat & approval ---

export type ChatMessageRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: ChatMessageRole;
  content: string;
  sessionId?: string;
  /** True when the answer was produced by local Ollama fallback (cloud LLM unavailable). */
  usedOllamaFallback?: boolean;
}

export type OutputMode = "chat" | "json";

export interface ApprovalChunkPayload {
  id: number | null;
  document_id: number | null;
  text: string;
  source_page: number | null;
  source_slide: number | null;
  source_sheet: number | null;
  source_timestamp_start: number | null;
  source_timestamp_end: number | null;
  section_title: string | null;
}

export interface SSEMetaEvent {
  type: "meta";
  session_id: string;
  document_id: number | null;
  language: string;
  require_approval: boolean;
  retrieved_chunk_count: number;
  active_regulations: string[];
}

export interface SSEApprovalRequiredEvent {
  type: "approval_required";
  session_id: string;
  masked_prompt?: string;
  chunks: ApprovalChunkPayload[];
}

export interface SSEApprovalRejectedEvent {
  type: "approval_rejected";
  session_id: string;
  reason?: string;
  timed_out: boolean;
}

export interface SSEAnswerChunkEvent {
  type: "answer_chunk";
  text: string;
}

export interface SSEEndEvent {
  type: "end";
  used_ollama_fallback?: boolean;
}

export interface SSEErrorEvent {
  type: "error";
  message: string;
}

export type SSEChatEvent =
  | SSEMetaEvent
  | SSEApprovalRequiredEvent
  | SSEApprovalRejectedEvent
  | SSEAnswerChunkEvent
  | SSEEndEvent
  | SSEErrorEvent;

export interface AppSettingsResponse {
  id: number;
  llm_provider: string;
  llm_model: string;
  ollama_base_url: string;
  ollama_chat_model: string;
  ollama_deanon_model: string;
  deanon_enabled: boolean;
  deanon_strategy: string;
  require_approval: boolean;
  show_json_output: boolean;
  use_presidio_layer: boolean;
  use_ner_layer: boolean;
  use_ollama_layer: boolean;
  chunk_size: number;
  chunk_overlap: number;
  top_k_retrieval: number;
  pdf_chunk_size: number;
  audio_chunk_size: number;
  spreadsheet_chunk_size: number;
  whisper_model: string;
  image_ocr_languages: string[];
  ocr_provider: string;
  ocr_provider_options: Record<string, unknown> | null;
  extract_embedded_images: boolean;
  recursive_email_attachments: boolean;
  default_active_regulations: string[];
  ner_model_overrides: Record<string, string> | null;
}

