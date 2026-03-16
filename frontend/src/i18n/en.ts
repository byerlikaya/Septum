export const enMessages = {
  "sidebar.appName": "Septum",
  "sidebar.tagline": "AI Privacy Gateway",
  "sidebar.chat": "Chat",
  "sidebar.documents": "Documents",
  "sidebar.chunks": "Chunks",
  "sidebar.settings": "Settings",
  "sidebar.regulations": "Regulations",
  "sidebar.error logs": "Error Logs",
  "sidebar.footer": "Privacy-first · Local-first",

  "errorLogs.title": "Error Logs",
  "errorLogs.subtitle":
    "View and clear backend and frontend errors recorded by the application.",
  "errorLogs.loading": "Loading error logs…",
  "errorLogs.empty": "No error logs.",
  "errorLogs.clearAll": "Clear all logs",
  "errorLogs.clearing": "Clearing…",
  "errorLogs.confirm.clearAll":
    "Are you sure you want to clear all error logs? This action cannot be undone.",
  "errorLogs.filter.source": "Source",
  "errorLogs.filter.allSources": "All sources",
  "errorLogs.filter.level": "Level",
  "errorLogs.filter.allLevels": "All levels",
  "errorLogs.column.time": "Time",
  "errorLogs.column.source": "Source",
  "errorLogs.column.level": "Level",
  "errorLogs.column.message": "Message",
  "errorLogs.column.path": "Path",
  "errorLogs.column.status": "Status",
  "errorLogs.showDetail": "Details",
  "errorLogs.hideDetail": "Hide",
  "errorLogs.stackTrace": "Stack trace",
  "errorLogs.extra": "Extra",
  "errorLogs.noDetail": "No stack trace or extra data.",
  "errorLogs.paginationSummary": "{total} total · page {page} of {totalPages}",
  "errorLogs.prevPage": "Previous",
  "errorLogs.nextPage": "Next",
  "errorLogs.badgeAriaLabel": "{count} errors logged",

  "chat.title": "Chat",
  "chat.subtitle":
    "Interact with Septum's privacy-preserving assistant. Select documents and ask questions; responses stream in real time.",
  "chat.uploading": "Uploading document…",
  "chat.uploadSuccess": "Document uploaded successfully.",
  "chat.uploadError": "Document upload failed. Please try again.",
  "chat.loadingSettings": "Loading settings…",

  "documents.title": "Documents",
  "documents.subtitle": "Upload, inspect, and manage ingested documents.",
  "documents.uploading": "Uploading documents…",
  "documents.table.loading": "Documents are loading…",
  "documents.table.empty": "No documents uploaded yet.",
  "documents.table.column.document": "Document",
  "documents.table.column.type": "Type",
  "documents.table.column.size": "Size",
  "documents.table.column.status": "Status",
  "documents.table.column.chunks": "Chunks",
  "documents.table.column.entities": "Entities",
  "documents.table.column.actions": "Actions",
  "documents.table.languageLabel": "Language",
  "documents.table.regulationsLabel": "Regulations",
  "documents.status.completed": "Completed",
  "documents.status.processing": "Processing",
  "documents.status.pending": "Pending",
  "documents.status.failed": "Failed",
  "documents.actions.preview": "Preview",
  "documents.actions.transcription": "Transcription",
  "documents.actions.delete": "Delete",
  "documents.actions.deleteAll": "Delete all documents",
  "documents.actions.deletingAll": "Deleting all…",
  "documents.confirm.delete":
    'Are you sure you want to delete "{name}"?',
  "documents.confirm.deleteAll":
    "Are you sure you want to delete all documents? This action cannot be undone.",

  "chunks.title": "Chunks",
  "chunks.subtitle": "Expand a document below to view and edit its sanitized chunks.",
  "chunks.loadingDocuments": "Loading documents…",
  "chunks.emptyHint":
    "No documents with chunks yet. Upload and ingest a document from the Documents page first.",
  "chunks.search.label": "Ask a question about chunks",
  "chunks.search.placeholder": "Type your question here…",
  "chunks.search.documentLabel": "Search within document",
  "chunks.search.documentPlaceholder": "Select a document to search",
  "chunks.search.button": "Search chunks",
  "chunks.search.searching": "Searching…",
  "chunks.search.resultsTitle": "Search results ({count})",
  "chunks.search.clear": "Clear results",
  "chunks.search.noResults": "No chunks matched this question.",

  "settings.title": "Settings",
  "settings.subtitle":
    "Configure cloud LLMs, privacy layers, local models, RAG, and ingestion.",
  "settings.loading": "Settings are loading...",
  "settings.tabs.cloud.label": "Cloud LLM",
  "settings.tabs.cloud.description": "Provider & model",
  "settings.tabs.privacy.label": "Privacy & Sanitization",
  "settings.tabs.privacy.description": "Approval & masking",
  "settings.tabs.local.label": "Local Models",
  "settings.tabs.local.description": "Ollama & de-anon",
  "settings.tabs.rag.label": "RAG",
  "settings.tabs.rag.description": "Chunking & retrieval",
  "settings.tabs.ingestion.label": "Ingestion",
  "settings.tabs.ingestion.description": "Whisper & OCR",
  "settings.tabs.textNormalization.label": "Text normalization",
  "settings.tabs.textNormalization.description": "Regex-based fixes",
  "settings.tabs.ner.label": "NER Models",
  "settings.tabs.ner.description": "Language model map",
  "settings.ner.sectionDescription":
    "View the default mapping from language codes to HuggingFace NER models. Persistence of overrides will be added in a later step.",
  "settings.ner.table.language": "Language",
  "settings.ner.table.model": "Model",
  "settings.ner.table.actions": "Actions",
  "settings.ner.overrideLabel": "Override model for {lang}",
  "settings.ner.restoreDefault": "Restore default",
  "settings.ner.saveOverrides": "Save overrides",
  "settings.common.saving": "Saving…",

  "settings.common.testing": "Testing...",
  "settings.common.testConnection": "Test Connection",

  "settings.cloud.sectionTitle": "Cloud LLM Settings",
  "settings.cloud.sectionDescription":
    "Configure your primary cloud LLM provider and model. These settings are used for all remote completions.",
  "settings.cloud.provider.hint":
    "Provider identifier used by the backend router.",
  "settings.cloud.model.hint":
    "Exact model ID as expected by your provider.",
  "settings.cloud.test.success": "Cloud LLM connectivity test succeeded.",
  "settings.cloud.test.failed": "Cloud LLM connectivity test failed.",

  "settings.privacy.sectionTitle": "Privacy & Sanitization",
  "settings.privacy.sectionDescription":
    "Control de-anonymization behaviour, approval gating, and which sanitization layers are active.",
  "settings.privacy.deanon.label": "De-anonymization enabled",
  "settings.privacy.deanon.description":
    "Allow local de-anonymization of placeholders before responses are shown.",
  "settings.privacy.deanonStrategy.label": "De-anonymization strategy",
  "settings.privacy.deanonStrategy.hint":
    "Strategy identifier (for example 'simple').",
  "settings.privacy.requireApproval.label": "Require approval by default",
  "settings.privacy.requireApproval.description":
    "Ask for explicit approval before sending masked chunks to cloud LLMs.",
  "settings.privacy.showJson.label": "Show JSON output",
  "settings.privacy.showJson.description":
    "Expose raw JSON payloads alongside chat responses for debugging.",
  "settings.privacy.layers.title": "Sanitization layers",
  "settings.privacy.layers.presidio.label": "Presidio layer",
  "settings.privacy.layers.presidio.description":
    "Rule-based recognizers and national ID validators.",
  "settings.privacy.layers.ner.label": "NER layer",
  "settings.privacy.layers.ner.description":
    "Language-specific HuggingFace NER models.",
  "settings.privacy.layers.ollama.label": "Ollama layer",
  "settings.privacy.layers.ollama.description":
    "Optional local LLM recognizers (future).",

  "settings.desktopAssistant.enabled.label": "Desktop Assistant Mode",
  "settings.desktopAssistant.enabled.description":
    "Allow sending chat messages directly to a local desktop assistant client (for example ChatGPT or Claude) instead of the cloud LLM behind Septum.",
  "settings.desktopAssistant.defaultTarget.label": "Default desktop assistant target",
  "settings.desktopAssistant.defaultTarget.chatgpt": "ChatGPT desktop app",
  "settings.desktopAssistant.defaultTarget.claude": "Claude desktop app",
  "settings.desktopAssistant.defaultTarget.hint":
    "This target is preselected in the chat screen when Desktop Assistant Mode is active. Users can change it per-session.",
  "settings.desktopAssistant.chatgptNewChat.label": "Start new ChatGPT conversation by default",
  "settings.desktopAssistant.chatgptNewChat.description":
    "When enabled, Desktop Assistant Mode sends the new-chat shortcut to the ChatGPT desktop app before pasting the message.",

  "settings.local.sectionTitle": "Local Model Settings",
  "settings.local.sectionDescription":
    "Configure the local Ollama endpoint and models used for chat and de-anonymization.",
  "settings.local.test.success": "Local model connectivity test succeeded.",
  "settings.local.test.failed": "Local model connectivity test failed.",
  "settings.local.baseUrl.hint":
    "Base URL for your local Ollama instance.",
  "settings.local.chatModel.hint":
    "Ollama model name used for local chat.",
  "settings.local.deanonModel.hint":
    "Ollama model name used for local de-anonymization.",

  "settings.rag.sectionTitle": "RAG Settings",
  "settings.rag.sectionDescription":
    "Tune chunk sizes and retrieval parameters for the vector store.",
  "settings.rag.defaultChunkSize.label": "Default chunk size",
  "settings.rag.defaultChunkSize.description":
    "Approximate character length for text chunks.",
  "settings.rag.chunkOverlap.label": "Chunk overlap",
  "settings.rag.chunkOverlap.description":
    "Number of overlapping characters between consecutive chunks.",
  "settings.rag.topK.label": "Top‑K retrieval",
  "settings.rag.topK.description":
    "Default number of chunks retrieved per query.",
  "settings.rag.formatSpecific.title": "Format-specific chunk sizes",
  "settings.rag.pdfChunkSize.label": "PDF chunk size",
  "settings.rag.pdfChunkSize.description":
    "Chunk size override for PDFs.",
  "settings.rag.audioChunkSize.label": "Audio chunk size (seconds)",
  "settings.rag.audioChunkSize.description":
    "Audio window length for transcription chunks.",
  "settings.rag.spreadsheetChunkSize.label": "Spreadsheet chunk size",
  "settings.rag.spreadsheetChunkSize.description":
    "Maximum cell count per spreadsheet chunk.",

  "settings.ingestion.sectionTitle": "Ingestion Settings",
  "settings.ingestion.sectionDescription":
    "Control Whisper transcription, OCR languages, and how attachments and embedded assets are handled.",
  "settings.ingestion.audioHealth.title": "Audio pipeline health",
  "settings.ingestion.audioHealth.description":
    "Checks whether ffmpeg and the configured Whisper model are available.",
  "settings.ingestion.audioHealth.installButton": "Install Whisper model",
  "settings.ingestion.audioHealth.installPending": "Installing…",
  "settings.ingestion.audioHealth.checking": "Checking…",
  "settings.ingestion.audioHealth.unknown": "unknown",
  "settings.ingestion.audioHealth.ffmpegHint":
    "Install ffmpeg manually (for example on macOS:",
  "settings.ingestion.health.readFailed":
    "Failed to read ingestion health status.",
  "settings.ingestion.health.installFailed":
    "Failed to install or load the Whisper model.",
  "settings.ingestion.whisperModel.label": "Whisper model",
  "settings.ingestion.whisperModel.hint":
    "Local Whisper model size for audio transcription.",
  "settings.ingestion.defaultAudioLanguage.label": "Default audio language",
  "settings.ingestion.defaultAudioLanguage.auto": "Auto-detect",
  "settings.ingestion.defaultAudioLanguage.hint":
    "When set, Whisper is told the audio language (e.g. Turkish), which improves transcription accuracy. Leave on Auto-detect if your files use mixed languages.",
  "settings.ingestion.ocrLanguages.label":
    "OCR languages (comma-separated)",
  "settings.ingestion.ocrLanguages.hint":
    "Language codes for the selected OCR engine (e.g. en, tr, de).",
  "settings.ingestion.extractImages.label": "Extract embedded images",
  "settings.ingestion.extractImages.description":
    "Extract and process images embedded in documents where possible.",
  "settings.ingestion.recursiveEmail.label": "Recursive email attachments",
  "settings.ingestion.recursiveEmail.description":
    "Recursively ingest attachments found inside email archives.",

  "settings.textNormalization.sectionTitle": "Text normalization rules",
  "settings.textNormalization.sectionDescription":
    "Define regex-based text normalization rules that are applied after sanitization. Use this to fix systematic OCR errors or apply project-specific text cleanups without changing raw content.",
  "settings.textNormalization.newRuleTitle": "New normalization rule",
  "settings.textNormalization.fields.name": "Rule name",
  "settings.textNormalization.fields.pattern": "Regex pattern",
  "settings.textNormalization.fields.replacement": "Replacement",
  "settings.textNormalization.fields.priority": "Priority",
  "settings.textNormalization.fields.isActive": "Rule is active",
  "settings.textNormalization.actions.create": "Create rule",
  "settings.textNormalization.actions.creating": "Creating…",
  "settings.textNormalization.table.name": "Name",
  "settings.textNormalization.table.pattern": "Pattern",
  "settings.textNormalization.table.replacement": "Replacement",
  "settings.textNormalization.table.priority": "Priority",
  "settings.textNormalization.table.active": "Active",
  "settings.textNormalization.status.active": "Active",
  "settings.textNormalization.status.inactive": "Inactive",
  "settings.textNormalization.empty":
    "No text normalization rules have been defined yet.",

  "errors.generic.load": "An error occurred while loading data.",
  "errors.documents.load": "An error occurred while loading documents.",
  "errors.documents.upload": "An error occurred while uploading the file(s).",
  "errors.documents.delete": "An error occurred while deleting the document.",
  "errors.documents.deleteAll":
    "An error occurred while deleting all documents.",
  "errors.chunks.loadDocuments": "An error occurred while loading documents.",
  "errors.chunks.loadChunks": "An error occurred while loading chunks.",
  "errors.chunks.search": "An error occurred while searching chunks.",
  "errors.settings.load": "An error occurred while loading settings.",
  "errors.settings.update": "An error occurred while updating the setting.",
  "errors.regulations.load": "An error occurred while loading regulation settings.",
  "errors.regulations.update": "An error occurred while updating regulation rules.",
  "errors.regulations.test": "An error occurred while testing the rule. If this is a regex rule, please ensure the pattern is valid.",
  "errors.regulations.save": "An error occurred while saving the rule.",
  "errors.regulations.delete": "An error occurred while deleting the rule.",
  "errors.preview.document": "An error occurred while loading the document preview.",
  "errors.preview.transcription": "An error occurred while loading the transcription preview.",

  "documents.upload.duplicates": "Skipped already uploaded file(s): {names}.",

  "uploader.title": "Drag and drop your files here",
  "uploader.subtitle":
    "PDF, Word, Excel, images, audio files, and other supported formats",
  "uploader.button": "Browse files",

  "preview.document.title": "Document Preview",
  "preview.document.loading": "Document preview is loading…",
  "preview.document.empty": "No preview content is available for this document.",
  "preview.transcription.title": "Audio Transcription",
  "preview.transcription.loading": "Transcription is loading…",
  "preview.transcription.empty": "No transcription text is available yet.",
  "preview.close": "Close",

  "chat.output.label": "Output:",
  "chat.output.tab.chat": "Chat",
  "chat.output.tab.json": "JSON",
  "chat.emptyState":
    "Select a document and type a message to start. Responses stream word by word.",
  "chat.input.placeholder": "Ask about your document…",
  "chat.button.stop": "Stop",
  "chat.button.send": "Send",
  "chat.button.upload": "Attach document",
  "chat.status.thinking": "Thinking",
  "chat.copy": "Copy",
  "chat.copied": "Copied",
  "chat.localFallbackBadge": "Answered via local model (cloud unavailable)",
  "chat.copyAnswer": "Copy answer",
  "chat.deanonBanner":
    "Responses are de-anonymized locally. Placeholders in the answer have been replaced with original values on your device only.",

  "chat.mode.label": "Mode",
  "chat.mode.cloud": "Cloud Mode",
  "chat.mode.desktop": "Desktop Assistant Mode",
  "chat.desktop.target.label": "Desktop assistant:",
  "chat.desktop.target.chatgpt": "ChatGPT desktop app",
  "chat.desktop.target.claude": "Claude desktop app",
  "chat.desktop.openNewChat": "Start a new chat when using ChatGPT",
  "chat.desktop.useRag": "Use document context (RAG)",
  "chat.desktop.status.sent.chatgpt": "Message sent to ChatGPT desktop app.",
  "chat.desktop.status.sent.claude": "Message sent to Claude desktop app.",
  "chat.desktop.status.error":
    "Desktop assistant error: {message}",

  "chat.debug.title": "Data sent to and returned from the cloud",
  "chat.debug.button": "Show data sent to cloud",
  "chat.debug.maskedPrompt": "Prompt sent to cloud (masked)",
  "chat.debug.maskedAnswer": "Answer returned from cloud (masked)",
  "chat.debug.finalAnswer": "Locally processed and displayed answer",

  "chat.documentSelector.hint":
    "Select at least one document to query. Only ready documents are listed.",
  "chat.documentSelector.empty":
    "No documents ready for chat. Upload and process documents first.",

  "chat.json.title": "JSON output",
  "chat.json.invalid": "Invalid JSON",
  "chat.json.notFound": "No JSON found in response",
  "chat.json.structuredTitle": "Structured view (from markdown):",
  "chat.json.empty": "No content yet.",
  "chat.json.rawTitle": "Raw response",

  "chat.approval.title": "Approve context before sending to LLM",
  "chat.approval.regulations":
    "This request is being processed under: {regs}.",
  "chat.approval.noRegulations": "No specific regulations are active.",
  "chat.approval.timeRemaining": "Time remaining: {seconds}s",
  "chat.approval.maskedPrompt.title": "Masked prompt (sanitized)",
  "chat.approval.maskedPrompt.empty": "(empty)",
  "chat.approval.chunks.title": "Retrieved chunks (editable)",
  "chat.approval.chunks.helper":
    "You may edit the sanitized chunk text before sending to the LLM.",
  "chat.approval.chunks.label": "Chunk {index}",
  "chat.approval.chunks.page": "Page {page}",
  "chat.approval.button.reject": "Reject",
  "chat.approval.button.approve": "Approve & continue",

  "chunks.error.save": "Unable to save changes to this chunk.",
  "chunks.error.delete": "Unable to delete this chunk.",
  "chunks.confirm.delete": "Are you sure you want to delete this chunk?",
  "chunks.card.label": "Chunk #{index}",
  "chunks.card.showLess": "Show less",
  "chunks.card.showMore": "Show more",
  "chunks.card.charCount": "{count} chars",
  "chunks.card.lang": "Lang: {lang}",
  "chunks.card.regs": "Regs: {regs}",
  "chunks.card.loadingChunks": "Loading chunks…",
  "chunks.card.noChunks": "No chunks for this document.",

  "chunks.entity.detectedUnder": "Detected under: {regs}",
  "chunks.entity.placeholder": "Detected entity placeholder",

  "common.saving": "Saving…",
  "common.save": "Save",
  "common.cancel": "Cancel",
  "common.edit": "Edit",
  "common.deleting": "Deleting…",
  "common.delete": "Delete",
  "common.close": "Close",

  "regulations.page.title": "Regulation Rules & Custom Rules",
  "regulations.page.subtitle":
    "Activate built-in regulation packs and define custom recognizers for your specific privacy policies.",
  "regulations.builtin.title": "Built-in Regulation Rulesets",
  "regulations.builtin.subtitle":
    "Toggle global privacy regulations on or off. Active regulations are merged into a single sanitizer policy.",
  "regulations.builtin.summary.active": "Active",
  "regulations.builtin.summary.entities": "Combined entity types",
  "regulations.builtin.loading": "Regulation rules are loading...",
  "regulations.builtin.entityCountSuffix": "entity types covered.",
  "regulations.builtin.officialLink": "View official text",
  "regulations.builtin.viewEntities": "View entity types",
  "regulations.builtin.hideEntities": "Hide entity types",
  "regulations.builtin.badge.builtin": "Built‑in",
  "regulations.builtin.region": "Region",

  "regulations.desc.gdpr":
    "Comprehensive data protection regulation for the European Union and EEA.",
  "regulations.desc.ccpa":
    "California data protection and privacy regulation.",
  "regulations.desc.hipaa":
    "US regulation governing protected health information (PHI).",
  "regulations.desc.lgpd":
    "Brazilian General Data Protection Law (LGPD).",
  "regulations.desc.kvkk":
    "Turkish Personal Data Protection Law (KVKK).",

  "regulations.custom.title": "Custom Rules",
  "regulations.custom.subtitle":
    "Define organization-specific entities using regex, keyword lists, or local LLM prompts. Custom rules are merged with built-in regulations.",
  "regulations.custom.addButton": "Add New Rule",
  "regulations.custom.loading": "Custom rules are loading...",
  "regulations.custom.empty":
    "No custom rules have been defined yet. Use “Add New Rule” to create your first rule.",
  "regulations.custom.table.name": "Name",
  "regulations.custom.table.entityType": "Entity Type",
  "regulations.custom.table.method": "Method",
  "regulations.custom.table.placeholder": "Placeholder",
  "regulations.custom.table.status": "Status",
  "regulations.custom.table.actions": "Actions",
  "regulations.custom.method.regex": "Regex pattern",
  "regulations.custom.method.keyword": "Keyword list",
  "regulations.custom.method.llm": "LLM prompt",
  "regulations.custom.status.active": "Active",
  "regulations.custom.status.inactive": "Inactive",
  "regulations.custom.action.edit": "Edit",

  "regulations.panel.createTitle": "New Custom Rule",
  "regulations.panel.editTitle": "Edit Custom Rule",
  "regulations.panel.description":
    "Define the entity label, detection method, and context words. You can test the rule on sample text before saving.",
  "regulations.panel.close": "Close",
  "regulations.panel.field.ruleName": "Rule Name",
  "regulations.panel.field.ruleName.placeholder": "Patient File Number",
  "regulations.panel.field.entityType": "Entity Type",
  "regulations.panel.field.entityType.placeholder": "PATIENT_FILE_NUMBER",
  "regulations.panel.field.entityType.helper":
    "Uppercase, underscore-separated entity identifier.",
  "regulations.panel.field.placeholderLabel": "Placeholder Label",
  "regulations.panel.field.placeholderLabel.placeholder": "PATIENT_FILE",
  "regulations.panel.field.placeholderLabel.helper":
    "Placeholders are generated from this label, for example [PATIENT_FILE_1].",
  "regulations.panel.field.detectionMethod": "Detection Method",
  "regulations.panel.method.regex.title": "Regex Pattern",
  "regulations.panel.method.regex.description": "Advanced patterns",
  "regulations.panel.method.regex.placeholder": "[A-Z]{2}-\\d{4}",
  "regulations.panel.method.regex.helper":
    "Must be compatible with Python regex; the backend validates the pattern before saving.",
  "regulations.panel.method.keyword.title": "Keyword List",
  "regulations.panel.method.keyword.description": "Fixed terms",
  "regulations.panel.method.keyword.placeholder":
    "Acme Corp, GlobalTech, Internal Project X",
  "regulations.panel.method.keyword.helper":
    "Enter the exact keywords expected to appear in the text.",
  "regulations.panel.method.llm.title": "LLM Prompt",
  "regulations.panel.method.llm.description": "Ollama-based",
  "regulations.panel.method.llm.placeholder":
    "Find all salary amounts mentioned in the text.",
  "regulations.panel.method.llm.helper":
    "This description is used to build an LLM-based recognizer via Ollama.",
  "regulations.panel.field.contextWords": "Context Words (optional)",
  "regulations.panel.field.contextWords.placeholder":
    "patient, file, ref, account",
  "regulations.panel.field.contextWords.helper":
    "Helper words that should increase the score when they appear nearby (separate with commas).",
  "regulations.panel.field.sample": "Test Sample",
  "regulations.panel.field.sample.placeholder":
    "Paste a sample text here to test your rule before saving.",
  "regulations.panel.ruleActive": "Rule active",
  "regulations.panel.button.delete": "Delete",
  "regulations.panel.button.deletePending": "Deleting...",
  "regulations.panel.button.test": "Test Rule",
  "regulations.panel.button.testPending": "Testing...",
  "regulations.panel.button.saveCreate": "Save & Activate",
  "regulations.panel.button.saveEdit": "Save Changes",
  "regulations.panel.button.savePending": "Saving...",
  "regulations.panel.test.idle": "",
  "regulations.panel.test.pending": "Rule test is running...",
  "regulations.panel.test.noSample":
    "Please provide sample text before running a test.",
  "regulations.panel.test.missingRequired":
    "Please fill in Name, Entity Type, and Placeholder Label before testing.",
  "regulations.panel.test.noRuleId":
    "Rule ID could not be resolved; the test cannot be executed.",
  "regulations.panel.test.noMatches": "No matches were found.",
  "regulations.panel.test.noMatchesLlm":
    "LLM-prompt-based custom recognizers are currently implemented as placeholders in the backend, so matches may not be returned yet.",
  "regulations.panel.test.successWithCount": "{count} match(es) found.",
  "regulations.panel.test.matchesTitle": "Test matches",
  "regulations.panel.save.missingRequired":
    "Please fill in Name, Entity Type, and Placeholder Label before saving.",

  "regulations.panel.match.scoreLabel": "score",

  "regulations.panel.delete.error": "An error occurred while deleting the rule.",

  "regulations.panel.test.error.generic":
    "An error occurred while testing the rule. If this is a regex rule, please ensure the pattern is valid.",

  "regulations.panel.save.error":
    "An error occurred while saving the rule.",

  "regulations.panel.toggle.aria":
    "Toggle rule active",

  "regulations.nonPii.title": "Non-PII Rules (Advanced)",
  "regulations.nonPii.subtitle":
    "Advanced rules for treating some spans (for example greetings or boilerplate) as non-PII so they are not masked. This list is intended only for advanced users; most setups do not need changes here.",
  "regulations.nonPii.loading": "Non-PII rules are loading...",
  "regulations.nonPii.empty":
    "No Non-PII rules are defined. The system will continue using its default data-driven behaviour.",
  "regulations.nonPii.table.patternType": "Pattern Type",
  "regulations.nonPii.table.pattern": "Pattern",
  "regulations.nonPii.table.languages": "Languages",
  "regulations.nonPii.table.entityTypes": "Entity Types",
  "regulations.nonPii.table.minScore": "Min. Score",
  "regulations.nonPii.table.status": "Status",
  "regulations.nonPii.anyLanguage": "All languages",
  "regulations.nonPii.anyEntity": "All entity types",

  "language.label": "Language",
  "language.english": "English",
  "language.turkish": "Turkish"
} as const;

