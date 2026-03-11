from __future__ import annotations

from typing import List


class PromptCatalog:
    """Central catalog for backend LLM prompts."""

    @staticmethod
    def sanitizer_alias_layer(normalized_text: str) -> str:
        """Prompt for Ollama-based alias/nickname detection in the sanitizer."""
        system_part = (
            "You are a PII detection assistant. Your ONLY job is to find "
            "entities that normal NER models miss: nicknames, aliases, codenames, "
            "indirect references to people, organizations referred to by informal names. "
            "Return ONLY valid JSON, no explanation. "
            "IMPORTANT: Always include leading articles (The, A, An) if they are "
            "part of the nickname or alias. For example 'The Big Fish' should be "
            "returned as 'The Big Fish', not 'Big Fish'."
        )
        user_part = (
            "Find all aliases, nicknames, and indirect person references in this text. "
            'Return JSON array: [{"text": "...", "start": N, "end": N, "type": "ALIAS"}]. '
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
            "Always prioritise answering the user's question as directly and concisely as possible. "
            "Avoid meta explanations, disclaimers, or restating the entire table unless the user explicitly asks for that detail.\n\n"
            f"{placeholder_list_str}"
            f"Active privacy regulations: {regulations_str}.\n\n"
            f"{schema_instruction}"
            "User question (sanitized):\n"
            f"{sanitized_query}\n\n"
            "Relevant context (sanitized):\n"
            f"{context_text or '[no retrieved context]'}"
            f"{output_instruction}"
        )

