# Septum — Custom Recognizer Rules

<p align="center">
  <a href="../readme.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Installation</strong></a>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Architecture</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Screenshots</strong></a>
</p>

---

Septum ships 17 regulation packs out of the box. **Custom rules** let you extend
detection without forking the codebase: add a regex, a context-anchored keyword
list, or an LLM-prompt rule and it joins the active sanitization pipeline
immediately. Rules live under **Settings → Regulations → Custom Rules** in the
dashboard and are stored in the local database.

This page is a working reference. Each detection method has a worked example
plus a copy-paste test recipe.

## Detection methods

Septum supports three detection methods. Pick the one that matches the shape of
the data:

| Method        | When to use                                                                                  | Cost        |
|:--------------|:---------------------------------------------------------------------------------------------|:------------|
| `regex`       | Structural identifiers with a stable shape (codes, prefixed numbers, formatted IDs).         | Microseconds |
| `keyword_list` | A short, finite list of literal strings that should always be redacted (project codenames, internal team names). | Microseconds |
| `llm_prompt`  | Free-text categories that defy regex (medical conditions, sensitive opinions, slang aliases). | ~Seconds via local Ollama |

Every rule has these common fields:

- **Name** — short label that appears in the audit log and detection list.
- **Entity type** — e.g. `INTERNAL_PROJECT_CODE`, `EMPLOYEE_BADGE`. Free-form;
  the placeholder format becomes `[<ENTITY_TYPE>_N]`.
- **Placeholder label** *(optional)* — overrides the placeholder shown in the
  masked text (default is the entity type).
- **Active** — toggles the rule without deleting it.
- **Sample text** — the in-panel test field used by the **Test** button.

## Method 1 — Regex

A regex rule scans the chunk text and emits a span for every match. This is the
fastest detection method and the one you should reach for first.

### Example: internal project code

A company tags every internal project with `PRJ-<year>-<5 digit code>`, e.g.
`PRJ-2024-04829`. These appear in HR letters, invoices, and Slack exports.

| Field             | Value                              |
|:------------------|:-----------------------------------|
| Name              | `Internal project code`            |
| Entity type       | `INTERNAL_PROJECT_CODE`            |
| Detection method  | `regex`                            |
| Pattern           | `\bPRJ-\d{4}-\d{5}\b`              |
| Placeholder label | `INTERNAL_PROJECT_CODE`            |
| Sample text       | `Bütçe PRJ-2024-04829 onaylandı.` |

After saving, click **Test**. The **Test** button calls
`POST /api/regulations/recognizers/{id}/test` with the sample, and a passing
test reports `1 match — PRJ-2024-04829 (score 0.85)`.

> **Tip — context anchoring**
> Wrap the pattern in `\b...\b` (word boundaries) and put any keyword in front
> of the actual ID (`(?:Project|PRJ)[-\s:]*(\d{4}-\d{5})`). Boundary anchors
> stop the regex from matching inside larger strings.

### Example: HR badge with checksum

If your IDs include a checksum, the regex still does the matching but you can
add the checksum check via a follow-up keyword rule that filters by context, or
write the recognizer in code. Pure-regex rules cannot validate checksums.

## Method 2 — Keyword list

A keyword rule emits a span for every literal hit. Each keyword is matched as a
word-boundary substring; case-insensitive by default.

### Example: codename redaction

A team uses internal codenames (`Project Bluebird`, `Operation Halcyon`,
`Goldfish Initiative`) in design docs. None of them is PII per any regulation,
but the company wants them redacted before any cloud LLM call.

| Field             | Value                                           |
|:------------------|:------------------------------------------------|
| Name              | `Internal codenames`                            |
| Entity type       | `INTERNAL_CODENAME`                             |
| Detection method  | `keyword_list`                                  |
| Keywords          | `Project Bluebird, Operation Halcyon, Goldfish Initiative` |
| Placeholder label | `INTERNAL_CODENAME`                             |
| Sample text       | `The Project Bluebird launch is delayed.`      |

After saving, **Test** confirms `1 match — Project Bluebird`.

> **Tip — keyword density**
> Keep the list under ~50 entries. Long lists slow ingestion linearly and the
> entire list is loaded into memory per recognizer. For a sweep across hundreds
> of terms, generate a regex with alternation instead.

## Method 3 — LLM prompt

LLM-prompt rules pass the chunk text plus an instruction to a local Ollama model
and parse the JSON response into spans. This is the right tool for categories
that defy regex — medical conditions, opinions, sensitive cultural markers,
informal aliases — at the cost of `~1–5s` per chunk under
`llama3.2:3b` and proportionally more for larger models.

### Example: medication mentions

A clinical document set carries free-text mentions of medications (brand names,
generic names, dose forms) that no Presidio recognizer covers.

| Field             | Value                                                                                        |
|:------------------|:---------------------------------------------------------------------------------------------|
| Name              | `Medication mentions`                                                                        |
| Entity type       | `MEDICATION`                                                                                 |
| Detection method  | `llm_prompt`                                                                                 |
| LLM prompt        | `Find every medication mention (brand or generic name, with or without dose). Return JSON: [{"text": "<mention>"}].` |
| Placeholder label | `MEDICATION`                                                                                 |
| Sample text       | `Patient has been on Coumadin 5 mg daily and ibuprofen as needed.`                          |

The **Test** button executes the prompt against the sample and reports
`2 matches — Coumadin 5 mg, ibuprofen` if the local model is healthy.

> **Tip — keep the JSON contract minimal**
> Prompts that ask for many fields (severity, dose, etc.) get less reliable
> JSON back. The recognizer only needs the matched text; everything else is
> noise that increases parse failures.

> **Tip — context window**
> Septum sends one chunk at a time (default `chunk_size = 800` chars). Phrase
> the prompt to operate on a "block of text" rather than "the whole document"
> so the model behaves consistently across chunks.

## Common fields & test loop

Every custom rule shares this set of secondary controls:

- **Context words** *(optional)* — additional words that must appear within the
  same chunk for the rule to fire. Use this to suppress a regex that otherwise
  produces too many false positives. Example: a rule for 11-digit
  identifiers can require `Müşteri` or `Customer` in context.
- **Active** — toggles without deleting.
- **Sample text** + **Test** — the inline regression check. Use this for every
  edit; it persists no data and is a fast way to verify both positive and
  negative cases.

The test endpoint internally calls the same code path that ingestion uses, so
"works in test" implies "works on real documents" assuming the chunk text looks
like the sample.

## Audit & debugging

Every detection produced by a custom recognizer:

1. Lands in the document's `entity_detections` table with the rule's
   `entity_type` and `placeholder` — visible in **Document preview → Detected
   entities**.
2. Surfaces as a row on **Settings → Audit Log** when ingestion completes,
   tagged with the rule's name in the `extra` field.
3. Goes through the standard absorption + dedup passes — a custom rule does
   **not** override built-in regulation detections. Span priority is decided
   by the same `_HIGH_PRIORITY_ENTITY_TYPES` table the rest of Septum uses.

If a rule fires in test but not on a real document, open the document preview
and check the chunk text — PDF extraction sometimes splits a target across
chunks, in which case raise the `chunk_size` or use a smaller `chunk_overlap`.
