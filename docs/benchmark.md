# Septum — Benchmark Results

<p align="center">
  <a href="../readme.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Installation</strong></a>
  &nbsp;·&nbsp;
  <strong>📈 Benchmark</strong>
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

All 17 built-in regulations active, evaluated across **five independent data sources plus two robustness probes**:

1. **Septum synthetic corpus** — **3,468 algorithmically generated PII values** across 23 entity types in **16 languages** (ar, de, en, es, fr, hi, it, ja, ko, nl, pl, pt, ru, th, tr, zh). The only way to cover checksummed IDs (valid Luhn, IBAN MOD-97, TCKN) that no public dataset carries, plus a 31-doc semantic-contextual subset (60 entities across DIAGNOSIS / MEDICATION / RELIGION / POLITICAL_OPINION / ETHNICITY / SEXUAL_ORIENTATION) that measures Ollama's unique contribution. Fixed seed — fully reproducible.
2. **Microsoft [presidio-evaluator](https://github.com/microsoft/presidio-research)** — 200 synthetic Faker sentences, the reference PII evaluation framework used by the Presidio team. Entity map excludes DATE_TIME / TIME / TITLE / SEX / CARDISSUER / NRP — labels that are not PII under any of Septum's 17 regulations — so the recall number reflects masking quality, not category mismatches.
3. **[Babelscape/wikineural](https://huggingface.co/datasets/Babelscape/wikineural)** — 50 Wikipedia held-out sentences × 9 languages. Caveat: the XLM-RoBERTa NER models Septum uses were trained on the related [WikiANN corpus](https://huggingface.co/datasets/unimelb-nlp/wikiann), so these numbers skew toward an upper bound on real-world performance for this language set.
4. **[ai4privacy/pii-masking-300k](https://huggingface.co/datasets/ai4privacy/pii-masking-300k)** — 50 validation samples × 6 languages (en/de/fr/es/it/nl). Modern PII-specific dataset built from scratch; the models Septum uses were NOT trained on it, so this is the closest the benchmark gets to a true out-of-distribution check.
5. **[CoNLL-2003](https://aclanthology.org/W03-0419/)** — 200 samples from the classical EN news-domain held-out test split. Not in any Septum-relevant training corpus.
6. **[DFKI-SLT/few-nerd](https://huggingface.co/datasets/DFKI-SLT/few-nerd)** (supervised split) — multi-domain Wikipedia NER used as a cross-domain robustness probe. Only person / organization / location map to Septum's PII categories.
7. **Robustness probes** — 15 PII-free paragraphs in 9 languages (false-positive rate) + 18 obfuscated PII inputs (leetspeak, Unicode homoglyphs, zero-width joiners, mixed-case emails, bracketed emails, escaped credit cards, line-wrapped TCKNs and IBANs, international phone formats, "at / dot" ASCII obfuscation).

<p align="center">
  <a href="#septum--benchmark-results"><img src="../assets/benchmark-f1-by-type.svg" alt="F1 Score by Entity Type" width="1100" /></a>
</p>

<p align="center">
  <a href="#septum--benchmark-results"><img src="../assets/benchmark-layer-comparison.svg" alt="Detection Accuracy by Pipeline Layer" width="820" /></a>
</p>

## Septum synthetic corpus (per-layer)

| Layer | Entities | Types | Precision | Recall | F1 |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Presidio (L1)** — patterns + validators (controlled + extended + adversarial) | 1,710 | 20 | 100% | 96.4% | 98.2% |
| **NER (L2)** — XLM-RoBERTa + ALL CAPS normalisation (16 languages) | 840 | 3 | 99.9% | 90.8% | 95.1% |
| **Ollama (L3)** — aya-expanse:8b (alias + semantic-contextual) | 918 | 9 | 99.9% | 90.6% | 95.0% |
| **Combined** | **3,468** | **23** | **99.9%** | **93.5%** | **96.6%** |

**Ollama semantic subset** (DIAGNOSIS / MEDICATION / RELIGION / POLITICAL_OPINION / ETHNICITY / SEXUAL_ORIENTATION — entity types Presidio and NER cannot express): 60 entities across 31 docs, **F1 96.6%** (Precision 98.3%, Recall 95.0%).

**Ollama ablation** — same 205-doc corpus (semantic + alias + NER) with Ollama OFF vs ON: **+3.49 pp recall, +1.95 pp F1**. This is the honest measurement of Ollama's marginal value; on the semantic subset alone Ollama lifts recall from near-zero to 95% because no other layer can express those categories.

## External reference datasets

| Source | Entities | Types | Precision | Recall | F1 |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Microsoft presidio-evaluator** (EN, synthetic Faker, 200 samples) | 326 | 11 | 98.2% | 66.6% | 79.3% |
| **Babelscape/wikineural** (9 langs × 50 = 450 samples, held-out Wikipedia NER) | 634 | 3 | 95.5% | 76.5% | 84.9% |
| **ai4privacy/pii-masking-300k** (6 langs, real OOD — not in training data) | 1,456 | 12 | 96.4% | 54.5% | 69.6% |
| **CoNLL-2003** (EN news, 372 PER/ORG/LOC scored + 35 MISC excluded by design) | 372 | 3 | 97.9% | 37.4% | 54.1% |
| **DFKI-SLT/few-nerd** (multi-domain Wikipedia, 200 test samples) | 361 | 3 | 95.7% | 68.4% | 79.8% |

CoNLL-2003 recall is partly structural: 35 of 407 gold entities (**8.6%**) are MISC-class — nationalities, events, works-of-art — that are not PII under any of Septum's 17 regulation packs and are intentionally excluded from scoring. The headline 54.1% reflects recall *after* MISC is dropped and combines (a) Septum's deliberate policy that bare place names in free news text are not PII by themselves ([GDPR Art. 4(1)](https://gdpr-info.eu/art-4-gdpr/) rationale) with (b) the conservative LOCATION / ORGANIZATION gates on short single-token mentions. Ai4Privacy exposes real gaps around USERNAME and fine-grained address sub-types that the current regulation packs do not target directly — that's actionable signal, not a rounding error.

<p align="center">
  <a href="#septum--benchmark-results"><img src="../assets/benchmark-external-validation.svg" alt="External validation — Septum synthetic vs presidio-evaluator vs wikineural vs ai4privacy vs CoNLL-2003 vs Few-NERD vs adversarial pack" width="820" /></a>
</p>

## Robustness

| Probe | Volume | Result |
|:---|:---:|:---:|
| **Clean-text false-positive rate** (15 PII-free paragraphs, 439 tokens across 9 languages) | 0 FP | **0.00 FP / 1k tokens** |
| **Adversarial pack** (18 realistic obfuscated PII inputs: leetspeak, Unicode homoglyphs, zero-width joiners, mixed-case emails, bracketed emails, escaped credit cards, line-wrapped TCKNs and IBANs, international phone formats, "at / dot" ASCII obfuscation) | 20 planted | P 100% · R 90.0% · **F1 94.7%** |

The adversarial pack is intentionally biased toward the attempts a real user or attacker would type — contrived triple-spaced IBANs and other "nobody would actually paste this" constructions are excluded so the score reflects real-world resilience rather than stress-test theatre. The 10% recall gap is where an obfuscation-normalising custom-rules layer would pay off.

## Per-language breakdown (Ollama pipeline)

<p align="center">
  <a href="#septum--benchmark-results"><img src="../assets/benchmark-per-language.svg" alt="Per-language detection accuracy under the full Ollama pipeline — 16 languages" width="900" /></a>
</p>

F1 is uniform and very high across Latin-script languages (EN 98.3%, DE 100%, ES 100%, FR 95.8%, IT 100%, NL 100%, PL 98.6%, PT 97.1%, RU 100%, TR 96.8%) and remains strong on Arabic (92.3%) and Hindi (100%). The honest weak spots are CJK and Thai: **Thai 87.1%, Korean 83.3%, Japanese 65.4%, Chinese 54.2%**. The CJK minimum-span-length floor is now language-aware (2 glyphs for ZH / JA / KO / TH, 3 elsewhere) which already lifted Chinese from 44.4% to 54.2%; closing the rest of the gap requires per-language NER fine-tuning, which is on the roadmap, not claimed to be solved today.

> NER (L2) detects ALL CAPS names (common in medical/legal documents) via
> automatic titlecase normalisation, and recognises organisation names.
> LOCATION output goes through a conservative gate (multi-word OR confidence
> ≥ 0.95) so common-noun mis-fires like Turkish "Doğum" or German form
> headers are filtered out while real placenames like "İstanbul", "Berlin"
> still pass through. Ollama (L3) validates candidates and catches aliases.
> Benchmark includes adversarial edge cases (spaced IBANs, dotted phone
> numbers, etc.) that lower Presidio recall to real-world levels.
> Reproducible: [`pytest packages/api/tests/benchmark_detection.py -v -s`](../packages/api/tests/benchmark_detection.py)

## Coverage & limitations

**No PII detection system is 100% accurate.** Septum's benchmark is
transparent about where it wins and where it does not:

- **LOCATION output passes through a multi-word-or-high-score gate** (same
  shape as ORGANIZATION_NAME). Multilingual XLM-RoBERTa models produce
  stochastic single-token LOC mis-fires on common nouns and form-field
  headers in every language Septum supports (Turkish "Doğum", German form
  headers, etc.); chasing those per-language via stopword lists does not
  scale across the 50+ locales the middleware must handle. The gate drops
  single-token spans below 0.95 confidence — real placenames like
  "İstanbul", "Berlin" routinely score 0.97+ and multi-word locations
  ("New York") bypass the score gate entirely. Structured address PII is
  additionally captured by Presidio's `StructuralAddressRecognizer` and
  the per-regulation POSTAL_ADDRESS / STREET_ADDRESS recognisers.
- **All 37 regulation entity types are detectable** — 21 via Presidio, 3
  via NER, 9 via Ollama, and the rest via parent-type coverage
  (FIRST_NAME by PERSON_NAME, CITY by LOCATION, etc.).
- **23 entity types are actively benchmarked** across 3,468 values in 16
  languages with adversarial edge cases.
- **Semantic types** (DIAGNOSIS, MEDICATION, RELIGION, POLITICAL_OPINION)
  are detected only by the Ollama layer and require a local LLM to be
  running.
- **Context-dependent recognisers** (DATE_OF_BIRTH, PASSPORT_NUMBER, SSN,
  TAX_ID) require contextual keywords near the value to reduce false
  positives. Multilingual keyword lists cover 8+ languages.
- **Adversarial formats** (spaced TCKNs, dotted phone numbers) show lower
  detection rates than controlled-format tests. Reported honestly in the
  benchmark.

**The Approval Gate is the safety net.** Before any text is sent to the
LLM, you see exactly what will be transmitted and can reject it.
Automated detection reduces risk; human review eliminates it.

Benchmark models: NER uses [`akdeniz27/xlm-roberta-base-turkish-ner`](https://huggingface.co/akdeniz27/xlm-roberta-base-turkish-ner) (TR)
and [`Davlan/xlm-roberta-base-wikiann-ner`](https://huggingface.co/Davlan/xlm-roberta-base-wikiann-ner) (all other languages). Ollama
layer uses [`aya-expanse:8b`](https://ollama.com/library/aya-expanse). Larger Ollama models generally improve
semantic detection at the cost of latency.

## Ollama model comparison (L3 layer)

The L3 semantic layer is model-pluggable; the harness accepts
`SEPTUM_BENCHMARK_OLLAMA_MODEL=<model>` to swap the candidate. Run the
multi-model driver and the table below will be filled from live results
on your hardware:

```bash
./scripts/benchmark_ollama_models.sh                          # default trio
./scripts/benchmark_ollama_models.sh llama3.2:3b qwen2.5:14b  # custom set
```

| Model | Params | VRAM ≈ | Precision | Recall | F1 | Notes |
|:---|:---:|:---:|:---:|:---:|:---:|:---|
| [`llama3.2:3b`](https://ollama.com/library/llama3.2) | 3 B | 3 GB | _TBD_ | _TBD_ | _TBD_ | Fastest, lowest semantic recall expected |
| [`aya-expanse:8b`](https://ollama.com/library/aya-expanse) | 8 B | 5 GB | 99.9% | 90.6% | 95.0% | Current default; multilingual-tuned |
| [`qwen2.5:14b`](https://ollama.com/library/qwen2.5) | 14 B | 9 GB | _TBD_ | _TBD_ | _TBD_ | Highest accuracy expected; ~3× latency vs 8 B |

Numbers above the per-layer table reflect the default `aya-expanse:8b`
run; results for the other models depend on the host (CPU / GPU,
Ollama version, system prompt locale) and are intentionally left
**TBD** until the user reports them with the runner script. Latency
varies linearly with parameter count on CPU; on a single 24 GB GPU,
14 B models still fit but the ~3× longer per-call cost dominates the
ingestion pipeline.

**Additional references:**

- Benchmark harness: [`packages/api/tests/benchmark_detection.py`](../packages/api/tests/benchmark_detection.py)
- Recognizer packs (per regulation): [`packages/core/septum_core/recognizers/`](../packages/core/septum_core/recognizers/)
- National ID validators (Luhn, Verhoeff, ISO 7064, TCKN mod-10/mod-11, CPF, NRIC): [`packages/core/septum_core/national_ids/`](../packages/core/septum_core/national_ids/)
- Per-regulation legal sources for every entity type: [`regulation-entity-sources.md`](../packages/core/docs/regulation-entity-sources.md)
- Microsoft Presidio: [microsoft/presidio](https://github.com/microsoft/presidio) · evaluator: [microsoft/presidio-research](https://github.com/microsoft/presidio-research)
- XLM-RoBERTa paper: [Conneau et al., *Unsupervised Cross-lingual Representation Learning at Scale* (ACL 2020)](https://aclanthology.org/2020.acl-main.747/)
- CoNLL-2003 shared task paper: [Tjong Kim Sang & De Meulder, 2003](https://aclanthology.org/W03-0419/)
- Few-NERD paper: [Ding et al., *Few-NERD: A Few-shot Named Entity Recognition Dataset* (ACL 2021)](https://aclanthology.org/2021.acl-long.248/)
- Ai4Privacy dataset card: [ai4privacy/pii-masking-300k](https://huggingface.co/datasets/ai4privacy/pii-masking-300k)
- Regulation primary sources: [GDPR](https://gdpr-info.eu/) · [KVKK (6698)](https://www.mevzuat.gov.tr/MevzuatMetin/1.5.6698.pdf) · [HIPAA](https://www.hhs.gov/hipaa/for-professionals/privacy/laws-regulations/index.html) · [CCPA / CPRA](https://oag.ca.gov/privacy/ccpa) · [LGPD](https://www.gov.br/anpd/pt-br) · [UK GDPR](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/) · [PIPEDA](https://www.priv.gc.ca/en/privacy-topics/privacy-laws-in-canada/the-personal-information-protection-and-electronic-documents-act-pipeda/)

---

<p align="center">
  <a href="../readme.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Installation</strong></a>
  &nbsp;·&nbsp;
  <strong>📈 Benchmark</strong>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Architecture</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Screenshots</strong></a>
</p>
