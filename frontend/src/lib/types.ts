export type IngestionStatus = "pending" | "processing" | "completed" | "failed";

export interface AuthUser {
  id: number;
  email: string;
  is_active: boolean;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: string;
}

export interface ChatSessionSummary {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  document_ids: number[] | null;
  message_count: number;
}

export interface ChatSessionDetail extends ChatSessionSummary {
  messages: { id: number; role: string; content: string; created_at: string }[];
}

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

export type DetectionMethod = "regex" | "keyword_list" | "llm_prompt";

export interface RegulationRuleset {
  id: string;
  display_name: string;
  region: string;
  description?: string | null;
  official_url?: string | null;
  entity_types: string[];
  is_builtin: boolean;
  is_active: boolean;
  custom_notes?: string | null;
}

export interface CustomRecognizer {
  id: number;
  name: string;
  entity_type: string;
  detection_method: DetectionMethod;
  pattern?: string | null;
  keywords?: string[] | null;
  llm_prompt?: string | null;
  context_words: string[];
  placeholder_label: string;
  is_active: boolean;
}

export interface NonPiiRule {
  id: number;
  pattern_type: "token" | "regex";
  pattern: string;
  languages: string[];
  entity_types: string[];
  min_score?: number | null;
  is_active: boolean;
}

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
  use_ollama_validation_layer: boolean;
  use_ollama_layer: boolean;

  chunk_size: number;
  chunk_overlap: number;
  top_k_retrieval: number;
  pdf_chunk_size: number;
  audio_chunk_size: number;
  spreadsheet_chunk_size: number;
  whisper_model: string;
  default_audio_language?: string | null;
  image_ocr_languages: string[];
  ocr_provider: string;
  ocr_provider_options: Record<string, unknown> | null;
  extract_embedded_images: boolean;
  recursive_email_attachments: boolean;
  default_active_regulations: string[];
  ner_model_overrides: Record<string, string> | null;
  setup_completed: boolean;
}

export interface AuditEvent {
  id: number;
  created_at: string;
  event_type: string;
  session_id: string | null;
  document_id: number | null;
  regulation_ids: string[];
  entity_types_detected: Record<string, number>;
  entity_count: number;
  extra: Record<string, unknown> | null;
}

export interface AuditListResponse {
  items: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
}

export interface ComplianceReport {
  document_id: number;
  total_pii_events: number;
  total_deanonymization_events: number;
  total_entities_detected: number;
  entity_type_breakdown: Record<string, number>;
  regulation_ids_used: string[];
  events: AuditEvent[];
}

