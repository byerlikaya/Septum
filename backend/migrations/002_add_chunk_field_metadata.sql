-- Migration: Add field metadata columns to chunks table
-- Date: 2026-03-12
-- Purpose: Support structured field extraction and key-value pair chunking

ALTER TABLE chunks ADD COLUMN chunk_type TEXT NOT NULL DEFAULT 'clause';
ALTER TABLE chunks ADD COLUMN field_label TEXT NULL;
ALTER TABLE chunks ADD COLUMN field_value TEXT NULL;
ALTER TABLE chunks ADD COLUMN field_type TEXT NULL;

-- Create index on chunk_type for faster filtering
CREATE INDEX IF NOT EXISTS idx_chunks_chunk_type ON chunks(chunk_type);

-- Create index on field_type for field-based queries
CREATE INDEX IF NOT EXISTS idx_chunks_field_type ON chunks(field_type) WHERE field_type IS NOT NULL;
