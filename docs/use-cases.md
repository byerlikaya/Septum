---
title: "Use Cases"
description: "Concrete deployment scenarios — legal contract review, HR analytics, healthcare summarisation, free-form chat, MCP integrations."
---

# Septum — Use Cases

Where teams actually plug Septum in. Each scenario starts with the
business problem, walks the data path through Septum end to end, and
calls out the specific compliance or efficiency win that justified
the deployment.

## Legal — contract review at scale

**The problem.** A law firm has thousands of client contracts. They
want an LLM to surface common termination clauses, pricing patterns,
data-handling commitments — but the contracts contain client names,
addresses, deal values, and personal IDs that the firm cannot send
to a public model under client-privilege and GDPR / KVKK obligations.

**The Septum path.**

1. Bulk-upload PDFs through the dashboard. Septum's ingestion pipeline
   detects each file's language, applies the regulation packs the
   firm has activated (GDPR + KVKK by default), and indexes
   anonymised chunks alongside an encrypted copy of the original.
2. The reviewer asks: *"What termination clauses appear most often
   across these contracts?"*
3. Septum masks the question (no PII here) and runs hybrid retrieval
   over the masked chunk index. The matched paragraphs already carry
   `[PERSON_1]`, `[ORGANIZATION_NAME_3]`, `[ADDRESS_2]` placeholders.
4. The approval gate shows the masked prompt and the chunks that will
   leave. The reviewer accepts.
5. The cloud LLM returns an answer using placeholders. Septum
   restores the real entities locally before display.
6. The audit module logs: which user, which documents, how many PII
   instances of each type were masked, which regulation packs were
   active. Plenty enough for an Article 30 record-of-processing.

**Why it lands.**
- Client privilege is preserved by code-review invariant: raw text
  cannot reach the gateway zone.
- Compliance team gets a standing audit trail with zero PII inside
  it — safe to ship to their existing SIEM.
- The lawyer keeps the conversational fluency of a top-tier LLM.

## HR — performance and talent analytics

**The problem.** HR wants to summarise review cycles, flag
performance trends, and draft 360 feedback letters using an LLM. The
underlying data — names, salaries, national IDs, home addresses,
manager comments — is exactly the data that must not leave the
machine under KVKK / GDPR.

**The Septum path.** Same shape as legal: upload review PDFs and
spreadsheets, ask analytical questions, see the masking on the
approval gate, accept, get an answer that preserves the trends
without exposing any single person.

A typical query: *"Across the engineering org's H1 reviews, which
behavioural themes show up most for high performers?"* The LLM sees
`[PERSON_*]` placeholders, never the actual names; the answer comes
back with real names restored locally.

**Why it lands.** HR analytics has been stuck behind the
"can-we-share-this-with-an-LLM?" gate for two years. Septum makes the
answer "yes, the masked version" — without HR teams writing custom
masking pipelines per query.

## Healthcare — clinical note summarisation

**The problem.** A hospital wants to use an LLM to summarise
discharge notes and condense longitudinal patient histories for
attending physicians. HIPAA forbids sending PHI (Protected Health
Information) to a non-BAA cloud LLM. Yet the value of LLM
summarisation in this context is undeniable.

**The Septum path with the semantic layer enabled.**

1. Upload patient records — including OCR'd scans of paper forms.
2. Activate Layer 3 (Ollama semantic detection) so the pipeline picks
   up not just structured PHI (names, MRNs, dates) but also semantic
   categories that pattern matching can't express: `DIAGNOSIS`,
   `MEDICATION`, `BIOMETRIC_ID`, `CLINICAL_NOTE`.
3. The attending asks: *"Summarise this patient's diabetes management
   over the last three years."*
4. The masked prompt + chunks travel to the cloud LLM. The model sees
   `"[PERSON_1] was first diagnosed with [DIAGNOSIS_1] in [DATE_1]
   and started [MEDICATION_2]…"` — clinically meaningful structure,
   zero PHI.
5. The de-anonymised summary appears in the attending's chart view.

**Why it lands.** HIPAA audits care about technical safeguards
(`§ 164.312`) — Septum provides them: encryption at rest, audit
controls, access controls, transmission security. The semantic layer
means the LLM can still reason about diagnoses and medications
without learning who the patient is.

## Free-form chat — masking what you typed

**The problem.** A marketing manager types a prompt that contains
real customer data — name, email, phone, address — to draft a
personalised email. They didn't upload a document; they typed the PII
directly. Most "privacy gateways" only mask documents and miss this
path entirely.

**The Septum path.**

1. The user types into chat: *"Draft a welcome email for Ahmet Yılmaz
   (ahmet@firma.com, TC: 12345678901, address: İstanbul Caddesi
   No:42)."*
2. Septum runs PII detection on **the message itself**, not just on
   uploaded documents. `PERSON_NAME`, `EMAIL_ADDRESS`,
   `NATIONAL_ID` (TCKN with checksum validation), `POSTAL_ADDRESS`
   are detected.
3. The cloud LLM sees:
   `"Draft a welcome email for [PERSON_1] ([EMAIL_ADDRESS_1], TC:
   [NATIONAL_ID_1], address: [POSTAL_ADDRESS_1])."`
4. The LLM writes a perfectly fluent draft using those placeholders.
5. Septum restores the real values locally; the user sees the final
   email with the actual customer's name in it, ready to send.

**Why it lands.** The whole point of Septum is "raw PII never
leaves the machine" — and that has to be true whether the PII rides
in on a document or as text the user just typed. Many compliance
incidents start with someone pasting customer data into a chat
window. Septum closes that loop.

## Developer integrations — MCP

**The problem.** A developer uses Claude Code, Cursor, or Windsurf
day to day. Their codebase contains real customer test data, real
internal endpoint URLs, real API keys lurking in `.env.example`
files. They want LLM assistance without those values reaching
Anthropic / OpenAI.

**The Septum path via MCP.**

1. Install `septum-mcp` once (`pip install septum-mcp` or
   `uvx septum-mcp`).
2. Add it to the editor's MCP config:
   ```json
   {
     "mcpServers": {
       "septum": { "command": "septum-mcp", "env": { "SEPTUM_REGULATIONS": "gdpr,kvkk" } }
     }
   }
   ```
3. The MCP server exposes six tools to the editor: `mask_text`,
   `unmask_response`, `detect_pii`, `scan_file`, `list_regulations`,
   `get_session_map`.
4. The editor can now call `mask_text` on any snippet before sending
   it to the cloud LLM, then `unmask_response` on the reply. The
   developer keeps their workflow; the air-gapped invariant moves with
   them.

**Why it lands.** MCP is an open protocol; the integration works for
every tool that speaks it (Claude Code, Cursor, Windsurf, Zed,
Cline, Continue, ChatGPT Desktop, plus the official Python /
TypeScript / Rust / Go / C# / Java SDKs). Adding privacy is one
config block; no editor patches, no custom plugins.

For the full MCP server reference — transports, deployment,
environment variables — see the [MCP guide](https://github.com/byerlikaya/Septum/blob/main/packages/mcp/readme.md).

---

Each scenario above is a real shape Septum is built for; the
underlying mechanism is the same three-layer detection pipeline plus
the approval gate, configured for different regulation sets. The
[Workflows](workflows) page goes deep on how each step works; the
[Architecture](architecture) page covers the security zones that make
the air-gapped guarantee possible.
