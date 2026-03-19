from __future__ import annotations

from typing import Any, List


class PromptCatalog:
    """Central catalog for backend LLM prompts."""

    @staticmethod
    def sanitizer_alias_layer(normalized_text: str) -> str:
        """Prompt for Ollama-based PII detection in the sanitizer.

        Focused on detecting actual person names, nicknames, and aliases
        that constitute personally identifiable information. Deliberately
        conservative to avoid tagging common words, legal terms, or
        document structure labels as PII.
        """
        system_part = (
            "You are a strict PII detection assistant. Your ONLY task is to "
            "find actual PERSON NAMES and ALIASES (nicknames, codenames) that "
            "identify real individuals. "
            "DO NOT tag: (1) common words, verbs, adjectives, or question "
            "words in any language, (2) job titles, role names, or department "
            "names, (3) legal or document structure terms, "
            "(4) generic location words or city names unless they appear as "
            "part of a person's name, (5) pronouns or demonstratives. "
            "Only tag text that is CLEARLY a personal name or a known "
            "alias/nickname for a specific person. When in doubt, do NOT tag. "
            "CRITICAL: Return the EXACT text as it appears in the input. "
            "Do NOT capitalize, modify, or add surnames. Copy the exact substring. "
            "Return ONLY a valid JSON array, no explanation."
        )
        user_part = (
            "Find ONLY actual person names and personal aliases/nicknames in "
            "this text. Do NOT tag common words, questions, legal terms, job "
            "titles, locations, or document structure labels. "
            "IMPORTANT: Return the exact text substring as it appears. "
            'Return JSON array: [{"text": "exact span", "type": "PERSON_NAME"|"ALIAS"}]. '
            "If nothing found return [].\n\nText:\n"
            f"{normalized_text}"
        )
        return f"System: {system_part}\n\nUser: {user_part}"

    @staticmethod
    def deanonymizer_ollama(entity_map_json: str, masked_text: str) -> str:
        """Prompt for Ollama-based de-anonymization using a placeholder→value map."""
        return (
            "Replace every placeholder token in the text with its corresponding "
            "value from the map. Return ONLY the final text, no explanation.\n\n"
            f"Map: {entity_map_json}\n\nText: {masked_text}"
        )

    @staticmethod
    def llm_custom_recognizer_prompt(
        entity_type: str,
        instruction: str,
        text: str,
    ) -> str:
        """Prompt for LLM-backed custom recognizers."""
        return (
            "You are a PII detection assistant. For the following instruction, "
            "find all matching spans in the text. Return a JSON array of objects "
            "with keys: start, end, text, entity_type. Use entity_type "
            f'"{entity_type}". Only return the JSON array, nothing else.\n\n'
            f"Instruction: {instruction}\n\nText:\n{text}"
        )

    @staticmethod
    def pii_validation_prompt(
        text: str,
        candidate_spans: list[dict[str, Any]],
        language: str,
        regulation_rules: str,
    ) -> str:
        """Prompt for Ollama validation layer: filter false-positive PII candidates.

        This prompt is regulation-aware and language-agnostic. It asks the model
        to distinguish genuine sensitive information from general terms (job titles,
        role names, city names in organizational context) based on active privacy
        regulations and the surrounding context.
        """
        from json import dumps

        candidates_json = dumps(candidate_spans, ensure_ascii=False, indent=2)

        return (
            "You are a privacy regulation expert. Your task is to validate "
            "whether candidate text spans are TRULY personally identifiable "
            "information (PII) under the active privacy regulations.\n\n"
            "CONTEXT:\n"
            f"- Document language: {language.upper()}\n"
            f"- Active regulations:\n{regulation_rules}\n\n"
            "CRITICAL RULES FOR VALIDATION:\n"
            "1. GENERIC ROLE/OCCUPATION TERMS are NOT PII:\n"
            "   - Common nouns denoting professional roles, employee categories, or service recipients\n"
            "   - Generic professional categories or occupational groups\n"
            "   - Role classifications that could apply to many individuals\n"
            "   → These are NOT PII unless directly combined with specific identifying information.\n\n"
            "2. JOB TITLES ALONE are NOT PII:\n"
            "   - Organizational positions, management levels, or professional roles\n"
            "   - Department-level positions or functional roles\n"
            "   → Only PII if part of a unique identifier combining title with a specific person.\n\n"
            "3. GEOGRAPHIC LOCATIONS IN ORGANIZATIONAL CONTEXT are NOT PII:\n"
            "   - Place names referring to organizational branches, offices, or facilities\n"
            "   - Addresses of organizations (not residential addresses of individuals)\n"
            "   - Office locations, headquarters, or regional identifiers\n"
            "   → Only PII if it uniquely identifies a person's residence or personal location.\n\n"
            "4. ORGANIZATIONAL/STRUCTURAL TERMS are NOT PII:\n"
            "   - Entity type designations (corporations, institutions, divisions)\n"
            "   - Document structure keywords (sections, chapters, articles)\n"
            "   - Administrative or legal vocabulary\n\n"
            "5. FIELD LABELS/DOCUMENT METADATA are NOT PII:\n"
            "   - Field headers or form prompts that precede data entry\n"
            "   - Table column names or structured data labels\n"
            "   - Document section titles or metadata tags\n\n"
            "6. POSSESSIVE/GRAMMATICAL CONSTRUCTIONS are NOT PII:\n"
            "   - Possessive or genitive forms of generic terms\n"
            "   - Grammatical inflections, case markers, or particles\n"
            "   - Articles, prepositions, or conjunctions adjacent to PII\n\n"
            "7. ONLY MARK AS PII IF:\n"
            "   ✓ Uniquely identifies or could identify a SPECIFIC individual\n"
            "   ✓ Contains actual personal data protected by regulations\n"
            "   ✓ Would cause privacy harm if exposed\n"
            "   ✗ NOT a general/categorical/descriptive term\n"
            "   ✗ NOT common knowledge or public organizational information\n\n"
            "CANDIDATE SPANS:\n"
            f"{candidates_json}\n\n"
            "FULL TEXT FOR CONTEXT:\n"
            f"{text}\n\n"
            "INSTRUCTIONS:\n"
            "Analyze each candidate span in the context of the full text and the document's language. "
            "Apply the validation rules above strictly. "
            "Be AGGRESSIVE in filtering out non-PII: when in doubt about whether something "
            "is generic vs. identifying, err on the side of NOT marking it as PII.\n\n"
            "YOUR RESPONSE:\n"
            "Return ONLY a valid JSON array of spans that are TRULY PII. "
            "Each span must have: text, entity_type, start, end.\n"
            "Format: [{\"text\": \"<value>\", \"entity_type\": \"<TYPE>\", \"start\": <int>, \"end\": <int>}]\n"
            "If NO spans are truly PII after validation, return: []\n"
            "Output ONLY the JSON array, no explanations or markdown."
        )

    @staticmethod
    def json_output_instruction() -> str:
        """Instruction appended to the chat prompt when JSON output mode is active."""
        return (
            "\n\n---\n"
            "REQUIRED: Reply with ONLY a single valid JSON object. No markdown headings, no bullet lists, "
            "no code fences, no text before or after. Example format:\n"
            '{"summary": "one paragraph", "type": "document type", "key_points": ["point1", "point2"]}\n'
            "Use only double quotes. Output nothing but this JSON object.\n---\n\n"
        )

    @staticmethod
    def spreadsheet_schema_instruction(column_descriptions: list[str]) -> str:
        """Instruction that describes the active spreadsheet schema to the LLM."""
        return (
            "Active spreadsheet schema (generic, no raw personal data): "
            + "; ".join(column_descriptions)
            + ".\n\n"
            "When the schema marks a column as a numeric measure, "
            "and the user asks for aggregate calculations (totals, sums, averages, minimums, maximums, counts), you MUST perform "
            "the requested calculation over all rows visible in the provided context instead of just repeating a single row. "
            "Respond with the final numeric result in natural language, without listing every row unless explicitly requested.\n\n"
        )

    @staticmethod
    def placeholder_list_instruction(placeholders: list[str]) -> str:
        """Instruction to guide the LLM when the query references anonymised placeholders."""
        return (
            "\n\nThe user question may be in any language. Interpret by intent. "
            "If they ask for a specific piece of information (e.g. a person's name, a date, a single value), "
            "reply with only the placeholder token(s) from the context that directly answer that question. "
            "If they ask which persons, organizations, or other named entities appear in the document, "
            "reply with a bullet list of the relevant placeholder tokens from: "
            + ", ".join(placeholders)
            + ". Do not list document wording, clause fragments, or other text—only placeholder tokens. "
            "Do not refuse; answer from the context. For any other question, use the context as usual.\n\n"
        )

    REFUSAL_PHRASES: tuple[str, ...] = (
        "cannot answer",
        "not defined",
        "cannot provide",
        "could you clarify",
        "rephrase your question",
        "cannot find",
        "no information",
        "not found",
    )
    """Phrases used to detect when the LLM refuses to answer a query.

    FUTURE: migrate to a database-driven configuration so that users can
    customise refusal detection per language.
    """

    @staticmethod
    def chat_user_prompt(
        language: str,
        regulations_str: str,
        sanitized_query: str,
        context_text: str,
        schema_instruction: str,
        placeholder_list_str: str,
        output_instruction: str,
    ) -> str:
        """Prompt for the main chat LLM call."""
        language_instruction = ""
        if language and language != "en":
            language_instruction = (
                f"IMPORTANT: The user's query is in {language.upper()} language. "
                f"You MUST respond in the same language ({language.upper()}).\n\n"
            )

        return (
            f"{language_instruction}"
            "You are a privacy-preserving assistant. Personal data in the context is "
            "replaced by placeholders in square brackets (e.g. [PERSON_1], [ORGANIZATION_2]). "
            "Never guess or reconstruct real values and never explain the anonymization mechanism to the user.\n\n"
            "CRITICAL RULES:\n"
            "1. ONLY answer if the exact information is present in the provided context below.\n"
            "2. If you cannot find the answer in the context, respond EXACTLY: \"I cannot find that information in the document.\"\n"
            "3. NEVER invent, guess, or extrapolate information not explicitly stated in the context.\n"
            "4. NEVER combine information from different placeholders (e.g., if the context shows [LOCATION_5] in one place and [LOCATION_13] elsewhere, do NOT say they are the same entity).\n"
            "5. When citing values, copy them EXACTLY as they appear in the context—do not rephrase or paraphrase field labels.\n\n"
            "Always prioritise answering the user's question as directly and concisely as possible. "
            "Avoid meta explanations, disclaimers, or restating the entire table unless the user explicitly asks for that detail.\n\n"
            "When the user asks about quantities, counts, or locations (for example, how many components exist and where they are), "
            "you MUST derive the answer only from the provided context, by identifying all distinct items mentioned, counting them, "
            "and listing their locations. Do not guess or merge items; if the context lists six separate airbags, answer with six and "
            "describe each one and its location. If the context is incomplete or ambiguous, state that clearly instead of inventing details.\n\n"
            "When the user asks to interpret, summarize, or explain the document or its contents as a whole, use the entire context "
            "provided and synthesize information across sections. Give a clear interpretation in natural language: state what kind of "
            "document it is, list all key findings and any diagnosis or conclusion that appears in the context, and explain what they "
            "mean. Do not only list field labels. Answer in the user's language, based only on the context, without inventing facts "
            "or medical conclusions not stated in the context. Do NOT respond with \"I cannot find that information in the document\" "
            "for such requests when the context contains document content—synthesize and interpret from the context instead.\n\n"
            "CRITICAL FOR MULTIPLE DOCUMENTS: When the context contains multiple documents (each marked with '--- Document: <filename> ---'), "
            "you MUST interpret or summarize EACH document individually with full detail. For EACH document: (1) identify it by name, "
            "(2) extract and present ALL key findings, diagnoses, measurements, results, and conclusions from that document's chunks, "
            "(3) provide a complete interpretation. Do NOT skip any document. Do NOT provide only superficial summaries. If a document's "
            "chunks contain diagnostic or clinical information, you MUST present that information in your answer. After covering all documents "
            "individually, compare or relate them if relevant.\n\n"
            f"{placeholder_list_str}"
            f"Active privacy regulations: {regulations_str}.\n\n"
            f"{schema_instruction}"
            "User question (sanitized):\n"
            f"{sanitized_query}\n\n"
            "Relevant context (sanitized):\n"
            f"{context_text or '[no retrieved context]'}"
            f"{output_instruction}"
        )

