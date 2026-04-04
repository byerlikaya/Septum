"""Comprehensive PII detection benchmark for Septum.

Measures precision, recall, and F1 per entity type using programmatically
generated synthetic documents.  All PII values are algorithmically valid
(Luhn checksums, IBAN MOD-97, TCKN checksums) and generated with a fixed
seed for full reproducibility.

Three benchmark tiers:

  Layer 1  — Presidio (pattern-based):  8 entity types, 1 200 planted values
  Layer 2  — NER (HuggingFace XLM-RoBERTa):  PERSON_NAME + LOCATION
  Layer 3/4 — Ollama (aya-expanse:8b):  alias/nickname detection + validation

All 17 built-in regulations are activated during benchmarking.

Run everything:           pytest tests/benchmark_detection.py -v -s
Presidio only (fast):     pytest tests/benchmark_detection.py -v -s -k presidio
NER only:                 pytest tests/benchmark_detection.py -v -s -k ner
Ollama only:              pytest tests/benchmark_detection.py -v -s -k ollama
"""

from __future__ import annotations

import logging
import random
import string
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pytest

from backend.app.models.settings import AppSettings
from backend.app.seeds.regulations import builtin_regulations
from backend.app.services.anonymization_map import AnonymizationMap
from backend.app.services.policy_composer import PolicyComposer
from backend.app.services.sanitizer import PIISanitizer

logger = logging.getLogger(__name__)

_RNG = random.Random(42)
OLLAMA_MODEL = "aya-expanse:8b"
N = 150  # samples per entity type


# ═══════════════════════════════════════════════════════════════════════════
#  DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class PlantedEntity:
    original: str
    entity_type: str


@dataclass
class BenchmarkDocument:
    name: str
    text: str
    language: str
    planted: List[PlantedEntity]
    category: str = ""


@dataclass
class EntityMetrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class BenchmarkReport:
    per_type: Dict[str, EntityMetrics] = field(default_factory=dict)
    total_documents: int = 0
    layer_name: str = ""


# ═══════════════════════════════════════════════════════════════════════════
#  VALUE GENERATORS
# ═══════════════════════════════════════════════════════════════════════════


def _generate_tckn(first_nine: str) -> str:
    d = [int(c) for c in first_nine]
    d10 = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    d.append(d10)
    d.append(sum(d) % 10)
    return "".join(str(x) for x in d)


def _generate_tckns(n: int) -> list[str]:
    seen: set[str] = set()
    while len(seen) < n:
        first = str(_RNG.randint(1, 9))
        rest = "".join(str(_RNG.randint(0, 9)) for _ in range(8))
        seen.add(_generate_tckn(first + rest))
    return sorted(seen)


def _luhn_check_digit(partial: str) -> str:
    total = 0
    for i, d in enumerate(reversed([int(c) for c in partial])):
        v = d * 2 if i % 2 == 0 else d
        total += v - 9 if v > 9 else v
    return str((10 - (total % 10)) % 10)


def _generate_credit_cards(n: int) -> list[str]:
    prefixes = [("4", 16), ("51", 16), ("52", 16), ("53", 16), ("54", 16),
                ("55", 16), ("37", 15), ("34", 15), ("6011", 16),
                ("3528", 16), ("3529", 16)]
    cards: list[str] = []
    while len(cards) < n:
        prefix, length = prefixes[len(cards) % len(prefixes)]
        partial = prefix + "".join(str(_RNG.randint(0, 9)) for _ in range(length - len(prefix) - 1))
        cards.append(partial + _luhn_check_digit(partial))
    return cards


def _compute_iban(country: str, bban: str) -> str:
    """Compute a valid IBAN with correct check digits (ISO 7064 MOD 97-10)."""
    s = bban + country + "00"
    numeric = ""
    for c in s:
        numeric += str(ord(c.upper()) - 55) if c.isalpha() else c
    check = 98 - (int(numeric) % 97)
    return f"{country}{check:02d}{bban}"


def _generate_ibans(n: int) -> list[str]:
    # All-numeric BBANs across 13 countries.  Check digits computed via
    # ISO 7064 MOD 97-10 (verified against IBANValidator in standalone test).
    fmts = [("DE", 18), ("FR", 23), ("TR", 22), ("BE", 12), ("AT", 16),
            ("ES", 20), ("SE", 20), ("DK", 14), ("NO", 11), ("FI", 14),
            ("PL", 24), ("PT", 21), ("CH", 17)]
    ibans: list[str] = []
    while len(ibans) < n:
        country, bban_len = fmts[len(ibans) % len(fmts)]
        bban = "".join(str(_RNG.randint(0, 9)) for _ in range(bban_len))
        ibans.append(_compute_iban(country, bban))
    return ibans


def _generate_emails(n: int) -> list[str]:
    locals_ = ["john.smith", "sarah.j", "m.garcia", "a.mueller", "li.wei",
               "emma.w", "carlos.s", "yuki.t", "priya.s", "hans.sch",
               "maria.r", "olga.p", "james.t", "fatima.a", "chen.m",
               "anna.sv", "pedro.si", "sophie.d", "raj.p", "elena.po",
               "k.wat", "david.b", "nina.k", "ahmed.h", "julia.f",
               "hr", "billing", "support", "admin", "sales",
               "legal", "compliance", "info", "contact", "devops",
               "ops", "finance", "marketing", "ceo", "board"]
    domains = ["company.com", "acme-corp.com", "enterprise.co.uk", "firma.de",
               "exemple.fr", "empresa.com.br", "azienda.it", "bedrijf.nl",
               "foretag.se", "virksomhed.dk", "selskap.no", "yritys.fi",
               "spolka.pl", "empresa.es", "companhia.pt", "sirket.com.tr",
               "kaisha.co.jp", "gongsi.cn", "example.com", "mail.org",
               "service.io", "platform.dev", "cloud.net", "internal.corp",
               "global.tech"]
    emails: list[str] = []
    seen: set[str] = set()
    while len(emails) < n:
        local = _RNG.choice(locals_)
        r = _RNG.random()
        if r < 0.15:
            local += f"+tag{_RNG.randint(1, 999)}"
        elif r < 0.3:
            local += str(_RNG.randint(1, 99))
        email = f"{local}@{_RNG.choice(domains)}"
        if email not in seen:
            seen.add(email)
            emails.append(email)
    return emails


def _generate_phones(n: int) -> list[str]:
    """Generate phones in XXX XXX XX XX format (matches ExtendedPhoneRecognizer)."""
    country_codes = [
        "90", "44", "1", "49", "33", "34", "39", "31",
        "81", "61", "91", "55", "65", "82", "86", "7",
    ]
    phones: list[str] = []
    while len(phones) < n:
        cc = country_codes[len(phones) % len(country_codes)]
        d = [str(_RNG.randint(0, 9)) for _ in range(10)]
        # Format: +CC XXX XXX XX XX (matches the 3-3-2-2 pattern)
        phone = f"+{cc} {''.join(d[0:3])} {''.join(d[3:6])} {''.join(d[6:8])} {''.join(d[8:10])}"
        phones.append(phone)
    return phones


def _generate_ips(n: int) -> list[str]:
    ips: list[str] = []
    seen: set[str] = set()
    while len(ips) < n:
        kind = len(ips) % 4
        if kind == 0:
            ip = f"10.{_RNG.randint(0,255)}.{_RNG.randint(0,255)}.{_RNG.randint(1,254)}"
        elif kind == 1:
            ip = f"172.{_RNG.randint(16,31)}.{_RNG.randint(0,255)}.{_RNG.randint(1,254)}"
        elif kind == 2:
            ip = f"192.168.{_RNG.randint(0,255)}.{_RNG.randint(1,254)}"
        else:
            ip = f"{_RNG.randint(1,223)}.{_RNG.randint(0,255)}.{_RNG.randint(0,255)}.{_RNG.randint(1,254)}"
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


def _generate_mrns(n: int) -> list[str]:
    return [f"MRN-{_RNG.randint(100000, 9999999999):010d}" for _ in range(n)]


def _generate_health_ids(n: int) -> list[str]:
    return [
        _RNG.choice(string.ascii_uppercase) + _RNG.choice(string.ascii_uppercase)
        + "".join(str(_RNG.randint(0, 9)) for _ in range(_RNG.randint(6, 10)))
        for _ in range(n)
    ]


# ═══════════════════════════════════════════════════════════════════════════
#  NAME & LOCATION LISTS
# ═══════════════════════════════════════════════════════════════════════════

PERSON_NAMES_EN = [
    "John Smith", "Sarah Johnson", "Michael Williams", "David Brown",
    "Jennifer Davis", "Robert Wilson", "Emma Thompson", "James Anderson",
    "Emily Martinez", "William Taylor", "Jessica Moore", "Daniel Garcia",
    "Maria Lopez", "Kevin Chen", "Amanda White", "Thomas Nguyen",
    "Rachel Kim", "Christopher Morgan", "Elizabeth Carter", "Matthew Lee",
    "Olivia Harris", "Andrew Clark", "Sophia Lewis", "Ryan Walker",
    "Isabella Hall", "Alexander Petrov", "Sofia Rossi", "Hans Mueller",
    "Priya Sharma", "Oliver Grant", "Catherine Wells", "Pierre Dupont",
    "Anna Svensson", "Klaus Weber", "Lucia Fernandez", "Marco Bianchi",
    "Elena Popov", "Nikolai Volkov", "Ingrid Larsen", "Pablo Herrera",
    "Yuki Tanaka", "Wei Zhang", "Min-jun Park", "Aisha Khan",
    "Raj Patel", "Fatima Al-Hassan", "Kenji Nakamura", "Mei Lin",
    "Ananya Gupta", "Omar Saeed",
]

PERSON_NAMES_TR = [
    "Ahmet Yılmaz", "Mehmet Demir", "Fatma Kaya", "Ali Çelik",
    "Ayşe Şahin", "Mustafa Yıldız", "Zeynep Arslan", "Hasan Koç",
    "Elif Öztürk", "İbrahim Aydın", "Serkan Kara", "Deniz Yıldırım",
    "Gülşen Polat", "Murat Erdoğan", "Emre Özdemir", "Selin Aktaş",
    "Onur Demirtaş", "Ceren Aksoy", "Tolga Şen", "Burak Yılmaz",
    "Derya Korkmaz", "Hakan Güneş", "Esra Tekin", "Oğuz Başaran",
    "Pınar Doğan", "Berk Avcı", "Gamze Şimşek", "Volkan Özer",
    "Neslihan Akın", "Cem Karaca", "Sibel Yalçın", "Taner Keskin",
    "Gökhan Ateş", "Ebru Kurt", "Ufuk Çetin", "Melis Tan",
    "Barış Güler", "İrem Coşkun", "Alper Bulut", "Defne Ergün",
    "Kaan Tunç", "Seda Bayrak", "Ozan Duman", "Asuman Topal",
    "Tarık Genç", "Yasemin Uçar", "Erdem Kılıç", "Burcu Sezer",
    "Koray Mutlu", "Ezgi Canan",
]

LOCATIONS_EN = [
    "London", "Paris", "Berlin", "Tokyo", "New York", "Singapore",
    "Sydney", "Toronto", "Amsterdam", "Madrid", "Rome", "Seoul",
    "Mumbai", "Dubai", "Moscow", "Cairo", "Stockholm", "Vienna",
    "Prague", "Warsaw", "Helsinki", "Dublin", "Brussels", "Zurich",
    "Frankfurt", "Milan", "Barcelona", "Lisbon", "Athens", "Bangkok",
    "Shanghai", "Beijing", "Osaka", "Jakarta", "Manila", "Nairobi",
    "Chicago", "Boston", "San Francisco", "Vancouver",
    "Germany", "France", "Japan", "Australia", "Canada", "Brazil",
    "India", "China", "Turkey", "Italy", "Spain", "Netherlands",
    "Sweden", "Norway", "Denmark", "Finland", "Poland", "Portugal",
    "Switzerland", "Ireland",
]

LOCATIONS_TR = [
    "İstanbul", "Ankara", "İzmir", "Antalya", "Bursa", "Adana",
    "Konya", "Gaziantep", "Trabzon", "Eskişehir", "Kayseri",
    "Mersin", "Diyarbakır", "Samsun", "Denizli", "Malatya",
    "Erzurum", "Aydın", "Balıkesir", "Elazığ", "Manisa",
    "Sakarya", "Tekirdağ", "Muğla", "Hatay", "Edirne",
    "Çanakkale", "Bolu", "Rize", "Artvin", "Tokat",
    "Yozgat", "Giresun", "Kastamonu", "Sinop", "Aksaray",
    "Kırklareli", "Bilecik", "Düzce", "Uşak",
]


# ═══════════════════════════════════════════════════════════════════════════
#  CONTEXT TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════

_CTX_EMAIL = [
    "Contact {v} for inquiries.", "Send documents to {v}.",
    "Notifications go to {v}.", "Admin at {v} handles requests.",
    "CC {v} on correspondence.", "Receipt forwarded to {v}.",
    "Report submitted via {v}.", "Escalations go to {v}.",
    "Summaries delivered to {v}.", "Recovery emails from {v}.",
]
_CTX_PHONE = [
    "Call {v} for urgent matters.", "Helpdesk: {v}.",
    "Dial {v} for support.", "Emergency: {v}.",
    "Customer line: {v}.", "Branch phone: {v}.",
    "Fax to {v}.", "Direct line: {v}.",
    "Reservations: {v}.", "Schedule via {v}.",
]
_CTX_IP = [
    "Server {v} has high CPU.", "Traffic from {v} flagged.",
    "Agent on {v} healthy.", "Connection from {v} refused.",
    "DNS at {v} normal.", "Packet loss to {v}.",
    "VPN endpoint {v}.", "Replica at {v} lagging.",
    "Deployed to {v}.", "Cert on {v} expiring.",
]
_CTX_CC = [
    "Payment on card {v}.", "Refund to {v}.",
    "Card {v} charged.", "Transaction on {v} pending.",
    "Backup card: {v}.", "Card {v} removed.",
    "Pre-auth on {v}.", "Recurring on {v}.",
    "Chargeback for {v}.", "Card {v} declined.",
]
_CTX_IBAN = [
    "Wire to {v}.", "Salary to IBAN {v}.",
    "Invoice lists {v}.", "Refund to {v}.",
    "Standing order to {v}.", "Treasury: {v}.",
    "Monthly transfer to {v}.", "Deposits to {v}.",
    "Escrow in {v}.", "Supplier updated to {v}.",
]
_CTX_TCKN = [
    "TC kimlik numarası {v} kayıtlıdır.", "Kayıt {v} ile eşleşmektedir.",
    "Başvuru sahibi: {v}.", "Dosyada TC: {v}.",
    "Kimlik bilgisi {v}.", "Poliçe sahibi TC {v}.",
    "Vergi numarası {v}.", "Hasta kaydı TC {v}.",
    "Üyelik TC {v}.", "Sözleşme tarafı: {v}.",
]
_CTX_MRN = [
    "Patient file {v}.", "Lab results for {v}.",
    "Referral for {v}.", "Discharge summary {v}.",
    "Prescription linked to {v}.", "Imaging for {v}.",
    "Consult notes in {v}.", "Follow-up for {v}.",
    "Pathology in {v}.", "Claim against {v}.",
]
_CTX_HID = [
    "Insurance {v} verified.", "Claim under {v}.",
    "Member {v} active.", "Reimbursement for {v}.",
    "Enrollment {v} confirmed.", "Pre-auth for {v}.",
    "Coverage check {v}.", "Beneficiary {v}.",
    "Referral under {v}.", "Deductible met for {v}.",
]
_CTX_NAME_EN = [
    "The report was prepared by {v}.", "According to {v}, the timeline is on track.",
    "{v} attended the board meeting.", "{v} signed the legal brief.",
    "{v} submitted the budget proposal.", "Feedback from {v} was incorporated.",
    "Audit findings presented by {v}.", "{v} will lead the initiative.",
    "Training conducted by {v}.", "{v} approved the vendor contract.",
]
_CTX_NAME_TR = [
    "Rapor {v} tarafından hazırlanmıştır.", "{v} toplantıda sunum yapmıştır.",
    "Sözleşme {v} tarafından imzalanmıştır.", "{v} bütçe teklifini sunmuştur.",
    "Denetim sonuçlarını {v} açıklamıştır.", "{v} projenin liderliğini üstlenecektir.",
    "Eğitim {v} tarafından düzenlenmiştir.", "{v} sözleşmeyi onaylamıştır.",
    "Değerlendirme {v} tarafından teslim edilmiştir.", "{v} toplantıya başkanlık etmiştir.",
]
_CTX_LOC_EN = [
    "The conference will be held in {v}.", "Our office in {v} reports strong growth.",
    "The shipment was dispatched from {v}.", "Operations in {v} are expanding.",
    "A new branch opened in {v} last quarter.", "The client is headquartered in {v}.",
    "Training sessions scheduled in {v}.", "The event takes place in {v}.",
    "Compliance audit completed in {v}.", "Regional manager based in {v}.",
]
_CTX_LOC_TR = [
    "Toplantı {v}'da gerçekleştirildi.", "{v}'daki ofisimiz büyüyor.",
    "Gönderi {v}'dan yola çıktı.", "{v}'da operasyonlar genişliyor.",
    "{v}'da yeni şube açıldı.", "Müşteri {v}'da yerleşik.",
    "{v}'da eğitim planlandı.", "Etkinlik {v}'da düzenlenecek.",
    "{v}'da denetim tamamlandı.", "Bölge müdürü {v}'da görevli.",
]


# ═══════════════════════════════════════════════════════════════════════════
#  DOCUMENT BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def _build_docs(
    values: list[str], entity_type: str, category: str,
    contexts: list[str], language: str = "en", per_doc: int = 10,
) -> list[BenchmarkDocument]:
    docs: list[BenchmarkDocument] = []
    for i in range(0, len(values), per_doc):
        chunk = values[i:i + per_doc]
        text = " ".join(contexts[(i + j) % len(contexts)].format(v=v) for j, v in enumerate(chunk))
        docs.append(BenchmarkDocument(
            name=f"{category}_{i // per_doc + 1:03d}", text=text,
            language=language, planted=[PlantedEntity(v, entity_type) for v in chunk],
            category=category,
        ))
    return docs


# ═══════════════════════════════════════════════════════════════════════════
#  GENERATE CORPORA
# ═══════════════════════════════════════════════════════════════════════════

PRESIDIO_DOCUMENTS: list[BenchmarkDocument] = (
    _build_docs(_generate_emails(N), "EMAIL_ADDRESS", "email", _CTX_EMAIL)
    + _build_docs(_generate_phones(N), "PHONE_NUMBER", "phone", _CTX_PHONE)
    + _build_docs(_generate_ips(N), "IP_ADDRESS", "ip", _CTX_IP)
    + _build_docs(_generate_credit_cards(N), "CREDIT_CARD_NUMBER", "cc", _CTX_CC)
    + _build_docs(_generate_ibans(N), "IBAN", "iban", _CTX_IBAN)
    + _build_docs(_generate_tckns(N), "NATIONAL_ID", "tckn", _CTX_TCKN, "tr")
    + _build_docs(_generate_mrns(N), "MEDICAL_RECORD_NUMBER", "mrn", _CTX_MRN)
    + _build_docs(_generate_health_ids(N), "HEALTH_INSURANCE_ID", "hid", _CTX_HID)
)

NER_DOCUMENTS: list[BenchmarkDocument] = (
    _build_docs(PERSON_NAMES_EN, "PERSON_NAME", "ner_name_en", _CTX_NAME_EN, "en", 5)
    + _build_docs(PERSON_NAMES_TR, "PERSON_NAME", "ner_name_tr", _CTX_NAME_TR, "tr", 5)
    + _build_docs(LOCATIONS_EN, "LOCATION", "ner_loc_en", _CTX_LOC_EN, "en", 5)
    + _build_docs(LOCATIONS_TR, "LOCATION", "ner_loc_tr", _CTX_LOC_TR, "tr", 5)
)

# Ollama alias documents: person introduced by full name, then referenced informally
OLLAMA_ALIAS_DOCUMENTS: list[BenchmarkDocument] = [
    BenchmarkDocument(
        name="alias_en_01", language="en", category="ollama_alias",
        text=(
            "John Smith presented the quarterly earnings report to the board. "
            "Smith noted that revenue exceeded expectations by fifteen percent. "
            "The board congratulated John on the strong performance and asked "
            "him to prepare a detailed action plan for the next quarter."
        ),
        planted=[
            PlantedEntity("John Smith", "PERSON_NAME"),
            PlantedEntity("Smith", "PERSON_NAME"),
            PlantedEntity("John", "PERSON_NAME"),
        ],
    ),
    BenchmarkDocument(
        name="alias_en_02", language="en", category="ollama_alias",
        text=(
            "Dr. Sarah Johnson examined the patient records thoroughly. "
            "Johnson recommended additional tests for three cases. "
            "Sarah signed the referral forms before the deadline."
        ),
        planted=[
            PlantedEntity("Sarah Johnson", "PERSON_NAME"),
            PlantedEntity("Johnson", "PERSON_NAME"),
            PlantedEntity("Sarah", "PERSON_NAME"),
        ],
    ),
    BenchmarkDocument(
        name="alias_en_03", language="en", category="ollama_alias",
        text=(
            "Michael Williams submitted the compliance report on Friday. "
            "Williams flagged two areas of concern in the audit findings. "
            "Michael will present the remediation plan next week."
        ),
        planted=[
            PlantedEntity("Michael Williams", "PERSON_NAME"),
            PlantedEntity("Williams", "PERSON_NAME"),
            PlantedEntity("Michael", "PERSON_NAME"),
        ],
    ),
    BenchmarkDocument(
        name="alias_tr_01", language="tr", category="ollama_alias",
        text=(
            "Ahmet Yılmaz projenin ilk aşamasını tamamlamıştır. "
            "Yılmaz ikinci aşama için kaynak planlaması yapmaktadır. "
            "Ahmet ekip toplantısında durumu sunacaktır."
        ),
        planted=[
            PlantedEntity("Ahmet Yılmaz", "PERSON_NAME"),
            PlantedEntity("Yılmaz", "PERSON_NAME"),
            PlantedEntity("Ahmet", "PERSON_NAME"),
        ],
    ),
    BenchmarkDocument(
        name="alias_tr_02", language="tr", category="ollama_alias",
        text=(
            "Elif Öztürk müşteri şikayetlerini incelemiştir. "
            "Öztürk üç kritik konuyu raporlamıştır. "
            "Elif çözüm önerilerini yönetime sunacaktır."
        ),
        planted=[
            PlantedEntity("Elif Öztürk", "PERSON_NAME"),
            PlantedEntity("Öztürk", "PERSON_NAME"),
            PlantedEntity("Elif", "PERSON_NAME"),
        ],
    ),
    BenchmarkDocument(
        name="alias_tr_03", language="tr", category="ollama_alias",
        text=(
            "Serkan Kara yıllık bütçe planını hazırlamıştır. "
            "Kara maliyet optimizasyonu konusunda önerilerde bulunmuştur. "
            "Serkan finans direktörüyle toplantı ayarlayacaktır."
        ),
        planted=[
            PlantedEntity("Serkan Kara", "PERSON_NAME"),
            PlantedEntity("Kara", "PERSON_NAME"),
            PlantedEntity("Serkan", "PERSON_NAME"),
        ],
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
#  FIXTURES — all 17 regulations active
# ═══════════════════════════════════════════════════════════════════════════

def _make_policy() -> "ComposedPolicy":
    """Activate all 17 built-in regulations and compose a unified policy.

    NOTE: 'IBAN' has a dedicated ValidatedIBANRecognizer but is not listed
    in any regulation's entity_types (regulations use BANK_ACCOUNT_NUMBER).
    We add it explicitly so the recognizer is exercised during benchmarking.
    """
    regs = builtin_regulations()
    for reg in regs:
        reg.is_active = True
    policy = PolicyComposer().compose_from_data(regs, [], [])
    if "IBAN" not in policy.entity_types:
        policy.entity_types.append("IBAN")
        policy.entity_types.sort()
    return policy


def _make_settings(*, use_ner: bool = False, use_ollama: bool = False) -> AppSettings:
    return AppSettings(
        id=1, llm_provider="anthropic", llm_model="claude-3-5-sonnet-latest",
        ollama_base_url="http://localhost:11434",
        ollama_chat_model=OLLAMA_MODEL, ollama_deanon_model=OLLAMA_MODEL,
        deanon_enabled=True, deanon_strategy="simple",
        require_approval=False, show_json_output=False,
        use_presidio_layer=True, use_ner_layer=use_ner,
        use_ollama_validation_layer=use_ollama, use_ollama_layer=use_ollama,
        chunk_size=800, chunk_overlap=200, top_k_retrieval=5,
        pdf_chunk_size=1200, audio_chunk_size=60, spreadsheet_chunk_size=200,
        whisper_model="base", image_ocr_languages=["en"],
        ocr_provider="paddleocr", ocr_provider_options=None,
        extract_embedded_images=True, recursive_email_attachments=True,
        default_active_regulations=["gdpr", "kvkk"],
    )


@pytest.fixture(scope="module")
def presidio_sanitizer() -> PIISanitizer:
    return PIISanitizer(settings=_make_settings(), policy=_make_policy())


@pytest.fixture(scope="module")
def ner_sanitizer() -> Optional[PIISanitizer]:
    try:
        s = PIISanitizer(settings=_make_settings(use_ner=True), policy=_make_policy())
        s.sanitize(text="Warm-up probe with no PII data inside this sentence.",
                    language="en", anon_map=AnonymizationMap(document_id=-1, language="en"))
        return s
    except Exception:
        logger.warning("NER models not available")
        return None


@pytest.fixture(scope="module")
def ollama_sanitizer() -> Optional[PIISanitizer]:
    try:
        from backend.app.services.ollama_client import call_ollama_sync
        resp = call_ollama_sync(prompt="Reply with OK")
        if not resp or "error" in resp.lower():
            return None
        s = PIISanitizer(
            settings=_make_settings(use_ner=True, use_ollama=True),
            policy=_make_policy(),
        )
        return s
    except Exception:
        logger.warning("Ollama not available — skipping Ollama benchmark")
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  BENCHMARK LOGIC
# ═══════════════════════════════════════════════════════════════════════════

def _evaluate(sanitizer: PIISanitizer, doc: BenchmarkDocument) -> Dict[str, EntityMetrics]:
    anon_map = AnonymizationMap(document_id=0, language=doc.language)
    result = sanitizer.sanitize(text=doc.text, language=doc.language, anon_map=anon_map)
    metrics: Dict[str, EntityMetrics] = {}
    detected_counts: Dict[str, int] = {}
    for p in doc.planted:
        m = metrics.setdefault(p.entity_type, EntityMetrics())
        if p.original not in result.sanitized_text:
            m.tp += 1
            detected_counts[p.entity_type] = detected_counts.get(p.entity_type, 0) + 1
        else:
            m.fn += 1
    for etype, cnt in result.entity_type_counts.items():
        if etype in detected_counts:
            surplus = cnt - detected_counts[etype]
            if surplus > 0:
                metrics.setdefault(etype, EntityMetrics()).fp += surplus
    return metrics


def _merge(target: Dict[str, EntityMetrics], source: Dict[str, EntityMetrics]) -> None:
    for k, s in source.items():
        t = target.setdefault(k, EntityMetrics())
        t.tp += s.tp; t.fp += s.fp; t.fn += s.fn


def _totals(per_type: Dict[str, EntityMetrics]) -> EntityMetrics:
    t = EntityMetrics()
    for m in per_type.values():
        t.tp += m.tp; t.fp += m.fp; t.fn += m.fn
    return t


def _planted(docs: list[BenchmarkDocument]) -> int:
    return sum(len(d.planted) for d in docs)


def _report(report: BenchmarkReport) -> str:
    lines: list[str] = []
    hdr = f"{'Entity Type':<26} | {'TP':>4} | {'FP':>4} | {'FN':>4} | {'Prec':>7} | {'Recall':>7} | {'F1':>7}"
    sep = "-" * len(hdr)
    planted = sum(m.tp + m.fn for m in report.per_type.values())
    lines += ["", f"  Layer: {report.layer_name}",
              f"  Documents: {report.total_documents}",
              f"  Planted entities: {planted}",
              f"  Entity types: {len(report.per_type)}", "",
              sep, hdr, sep]
    t = EntityMetrics()
    for etype in sorted(report.per_type):
        m = report.per_type[etype]
        t.tp += m.tp; t.fp += m.fp; t.fn += m.fn
        lines.append(f"{etype:<26} | {m.tp:>4} | {m.fp:>4} | {m.fn:>4} | "
                      f"{m.precision:>6.1%} | {m.recall:>6.1%} | {m.f1:>6.1%}")
    lines += [sep,
              f"{'TOTAL':<26} | {t.tp:>4} | {t.fp:>4} | {t.fn:>4} | "
              f"{t.precision:>6.1%} | {t.recall:>6.1%} | {t.f1:>6.1%}",
              sep]
    return "\n".join(lines)


def _run_benchmark(sanitizer: PIISanitizer, docs: list[BenchmarkDocument],
                   layer_name: str) -> BenchmarkReport:
    r = BenchmarkReport(layer_name=layer_name)
    for doc in docs:
        _merge(r.per_type, _evaluate(sanitizer, doc))
        r.total_documents += 1
    return r


# ═══════════════════════════════════════════════════════════════════════════
#  TESTS
# ═══════════════════════════════════════════════════════════════════════════

def test_benchmark_presidio_layer(presidio_sanitizer: PIISanitizer) -> None:
    """Presidio layer: 1 200 planted entities, 8 types, 17 regulations active."""
    r = _run_benchmark(presidio_sanitizer, PRESIDIO_DOCUMENTS,
                       "Presidio (Layer 1 — pattern-based, 17 regulations)")
    print(f"\n\n=== Presidio Layer Benchmark ===\n{_report(r)}\n")
    assert _totals(r.per_type).recall >= 0.70


def test_presidio_no_zero_recall(presidio_sanitizer: PIISanitizer) -> None:
    """Every Presidio entity type must detect at least one value."""
    r = _run_benchmark(presidio_sanitizer, PRESIDIO_DOCUMENTS, "")
    for etype, m in r.per_type.items():
        assert m.recall > 0, f"{etype}: 0% recall ({m.fn} planted)"


def test_benchmark_ner_layer(ner_sanitizer: Optional[PIISanitizer]) -> None:
    """NER layer: PERSON_NAME + LOCATION across EN and TR."""
    if ner_sanitizer is None:
        pytest.skip("NER models not available")
    r = _run_benchmark(ner_sanitizer, NER_DOCUMENTS,
                       "NER (Layer 2 — HuggingFace XLM-RoBERTa, 17 regulations)")
    print(f"\n\n=== NER Layer Benchmark ===\n{_report(r)}\n")
    assert _totals(r.per_type).recall >= 0.40


def test_benchmark_ollama_layer(ollama_sanitizer: Optional[PIISanitizer]) -> None:
    """Ollama layer: alias/nickname detection + validation (model: aya-expanse:8b)."""
    if ollama_sanitizer is None:
        pytest.skip("Ollama not available")
    docs = NER_DOCUMENTS + OLLAMA_ALIAS_DOCUMENTS
    r = _run_benchmark(ollama_sanitizer, docs,
                       f"Full Pipeline (L1+L2+L3 — Ollama {OLLAMA_MODEL}, 17 regulations)")
    print(f"\n\n=== Full Pipeline Benchmark (with Ollama {OLLAMA_MODEL}) ===\n{_report(r)}\n")
    assert _totals(r.per_type).recall >= 0.40


def test_benchmark_combined_summary(
    presidio_sanitizer: PIISanitizer,
    ner_sanitizer: Optional[PIISanitizer],
    ollama_sanitizer: Optional[PIISanitizer],
) -> None:
    """Print combined summary across all layers."""
    p_r = _run_benchmark(presidio_sanitizer, PRESIDIO_DOCUMENTS, "Presidio")
    p_t = _totals(p_r.per_type)
    p_n = _planted(PRESIDIO_DOCUMENTS)

    print("\n\n" + "=" * 68)
    print("  SEPTUM PII DETECTION BENCHMARK — COMBINED SUMMARY")
    print("  All 17 built-in regulations active")
    print("=" * 68)
    print(f"\n  Layer 1 — Presidio (pattern-based)")
    print(f"    Documents:  {p_r.total_documents}  |  Entities: {p_n}  |  Types: {len(p_r.per_type)}")
    print(f"    Precision: {p_t.precision:.1%}  |  Recall: {p_t.recall:.1%}  |  F1: {p_t.f1:.1%}")

    if ner_sanitizer is not None:
        n_r = _run_benchmark(ner_sanitizer, NER_DOCUMENTS, "NER")
        n_t = _totals(n_r.per_type)
        n_n = _planted(NER_DOCUMENTS)
        print(f"\n  Layer 2 — NER (HuggingFace XLM-RoBERTa)")
        print(f"    Documents:  {n_r.total_documents}  |  Entities: {n_n}  |  Types: {len(n_r.per_type)}")
        print(f"    Precision: {n_t.precision:.1%}  |  Recall: {n_t.recall:.1%}  |  F1: {n_t.f1:.1%}")
    else:
        n_t = None; n_n = 0
        print("\n  Layer 2 — NER: skipped (models not available)")

    if ollama_sanitizer is not None:
        o_docs = NER_DOCUMENTS + OLLAMA_ALIAS_DOCUMENTS
        o_r = _run_benchmark(ollama_sanitizer, o_docs, "Ollama")
        o_t = _totals(o_r.per_type)
        o_n = _planted(o_docs)
        print(f"\n  Layer 3/4 — Ollama ({OLLAMA_MODEL})")
        print(f"    Documents:  {o_r.total_documents}  |  Entities: {o_n}  |  Types: {len(o_r.per_type)}")
        print(f"    Precision: {o_t.precision:.1%}  |  Recall: {o_t.recall:.1%}  |  F1: {o_t.f1:.1%}")
    else:
        o_t = None; o_n = 0
        print(f"\n  Layer 3/4 — Ollama: skipped (server not available)")

    # Grand total
    all_tp = p_t.tp + (n_t.tp if n_t else 0) + (o_t.tp if o_t else 0)
    all_fp = p_t.fp + (n_t.fp if n_t else 0) + (o_t.fp if o_t else 0)
    all_fn = p_t.fn + (n_t.fn if n_t else 0) + (o_t.fn if o_t else 0)
    all_n = p_n + n_n + o_n
    pr = all_tp / (all_tp + all_fp) if (all_tp + all_fp) else 0
    rc = all_tp / (all_tp + all_fn) if (all_tp + all_fn) else 0
    f1 = 2 * pr * rc / (pr + rc) if (pr + rc) else 0
    print(f"\n  Grand Total")
    print(f"    Entities: {all_n}  |  Precision: {pr:.1%}  |  Recall: {rc:.1%}  |  F1: {f1:.1%}")
    print("\n" + "=" * 68 + "\n")
