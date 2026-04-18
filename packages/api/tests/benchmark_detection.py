"""Comprehensive PII detection benchmark for Septum.

Measures precision, recall, and F1 per entity type using programmatically
generated synthetic documents.  All PII values are algorithmically valid
(Luhn checksums, IBAN MOD-97, TCKN checksums) and generated with a fixed
seed for full reproducibility.

Three benchmark tiers:

  Layer 1  — Presidio (pattern-based):  20 entity types, 1 560 planted values
  Layer 2  — NER (HuggingFace XLM-RoBERTa):  PERSON_NAME (mixed case + ALL CAPS)
             + LOCATION + ORGANIZATION_NAME
  Layer 3/4 — Ollama (aya-expanse:8b):  alias/nickname detection + validation

ALL CAPS names test the titlecase normalisation path — transformer NER
models are trained on mixed-case text and need preprocessing to detect
names written in uppercase (common in medical reports, official forms).

All 17 built-in regulations are activated during benchmarking.

Side effects (combined_summary test only):
  - Screenshots regenerated: screenshots/benchmark-*.png
  - README.md and README.tr.md benchmark tables updated in-place

Run everything:           pytest tests/benchmark_detection.py -v -s
Presidio only (fast):     pytest tests/benchmark_detection.py -v -s -k presidio
NER only:                 pytest tests/benchmark_detection.py -v -s -k ner
Ollama only:              pytest tests/benchmark_detection.py -v -s -k ollama
"""

from __future__ import annotations

import logging
import random
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from septum_api.models.settings import AppSettings
from septum_api.seeds.regulations import builtin_regulations
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.policy_composer import PolicyComposer
from septum_api.services.sanitizer import PIISanitizer

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


def _generate_dates_of_birth(n: int) -> list[str]:
    """Generate dates in various formats: YYYY-MM-DD, DD/MM/YYYY, MM-DD-YYYY."""
    fmts = [
        "{y:04d}-{m:02d}-{d:02d}",
        "{d:02d}/{m:02d}/{y:04d}",
        "{m:02d}-{d:02d}-{y:04d}",
        "{d:02d}.{m:02d}.{y:04d}",
        "{y:04d}/{m:02d}/{d:02d}",
    ]
    days_per_month = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                      7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
    results: list[str] = []
    seen: set[str] = set()
    while len(results) < n:
        y = _RNG.randint(1950, 2005)
        m = _RNG.randint(1, 12)
        d = _RNG.randint(1, days_per_month[m])
        fmt = fmts[len(results) % len(fmts)]
        val = fmt.format(y=y, m=m, d=d)
        if val not in seen:
            seen.add(val)
            results.append(val)
    return results


def _generate_mac_addresses(n: int) -> list[str]:
    """Generate MAC addresses in colon and dash notation."""
    results: list[str] = []
    seen: set[str] = set()
    while len(results) < n:
        octets = [_RNG.randint(0, 255) for _ in range(6)]
        if len(results) % 2 == 0:
            mac = ":".join(f"{o:02X}" for o in octets)
        else:
            mac = "-".join(f"{o:02x}" for o in octets)
        if mac not in seen:
            seen.add(mac)
            results.append(mac)
    return results


def _generate_urls(n: int) -> list[str]:
    """Generate realistic HTTP/HTTPS URLs."""
    domains = [
        "example.com", "data-portal.org", "analytics.io", "platform.net",
        "enterprise.co", "services.tech", "cloud-app.dev", "secure-hub.com",
        "dashboard.info", "internal.corp",
    ]
    paths = [
        "/api/v1/users", "/dashboard/reports", "/docs/privacy-policy",
        "/account/settings", "/files/export", "/admin/logs",
        "/v2/health-check", "/portal/records", "/search?q=test",
        "/login?redirect=/home",
    ]
    results: list[str] = []
    seen: set[str] = set()
    while len(results) < n:
        scheme = "https" if len(results) % 3 != 0 else "http"
        domain = domains[len(results) % len(domains)]
        path = paths[_RNG.randint(0, len(paths) - 1)]
        url = f"{scheme}://{domain}{path}"
        if url not in seen:
            seen.add(url)
            suffix = _RNG.randint(1, 9999)
            url = f"{scheme}://{domain}{path}&sid={suffix}" if "?" in path else f"{scheme}://{domain}{path}/{suffix}"
            results.append(url)
    return results


def _generate_coordinates(n: int) -> list[str]:
    """Generate coordinates in decimal degrees and DMS formats."""
    results: list[str] = []
    while len(results) < n:
        lat = _RNG.uniform(-90, 90)
        lon = _RNG.uniform(-180, 180)
        if len(results) % 3 == 0:
            results.append(f"{lat:.6f}, {lon:.6f}")
        elif len(results) % 3 == 1:
            results.append(f"{lat:.4f}N, {abs(lon):.4f}{'W' if lon < 0 else 'E'}")
        else:
            lat_d = int(abs(lat))
            lat_m = int((abs(lat) - lat_d) * 60)
            lat_s = (abs(lat) - lat_d - lat_m / 60) * 3600
            lon_d = int(abs(lon))
            lon_m = int((abs(lon) - lon_d) * 60)
            lon_s = (abs(lon) - lon_d - lon_m / 60) * 3600
            lat_dir = "N" if lat >= 0 else "S"
            lon_dir = "E" if lon >= 0 else "W"
            results.append(f"{lat_d}\u00b0{lat_m}'{lat_s:.1f}\"{lat_dir} {lon_d}\u00b0{lon_m}'{lon_s:.1f}\"{lon_dir}")
    return results


def _generate_cookie_ids(n: int) -> list[str]:
    """Generate GA cookies and UUID session IDs."""
    results: list[str] = []
    while len(results) < n:
        if len(results) % 2 == 0:
            ts1 = _RNG.randint(1600000000, 1700000000)
            results.append(f"GA1.2.{_RNG.randint(100000000, 9999999999)}.{ts1}")
        else:
            parts = [
                f"{_RNG.randint(0, 0xFFFFFFFF):08x}",
                f"{_RNG.randint(0, 0xFFFF):04x}",
                f"4{_RNG.randint(0, 0xFFF):03x}",
                f"{_RNG.choice('89ab')}{_RNG.randint(0, 0xFFF):03x}",
                f"{_RNG.randint(0, 0xFFFFFFFFFFFF):012x}",
            ]
            results.append(f"session-{'-'.join(parts)}")
    return results


def _generate_device_ids(n: int) -> list[str]:
    """Generate IMEI (15-digit with Luhn) and UUID device IDs."""
    results: list[str] = []
    while len(results) < n:
        if len(results) % 2 == 0:
            partial = "".join(str(_RNG.randint(0, 9)) for _ in range(14))
            results.append(partial + _luhn_check_digit(partial))
        else:
            parts = [
                f"{_RNG.randint(0, 0xFFFFFFFF):08x}",
                f"{_RNG.randint(0, 0xFFFF):04x}",
                f"4{_RNG.randint(0, 0xFFF):03x}",
                f"{_RNG.choice('89ab')}{_RNG.randint(0, 0xFFF):03x}",
                f"{_RNG.randint(0, 0xFFFFFFFFFFFF):012x}",
            ]
            results.append("-".join(parts))
    return results


def _generate_ssns(n: int) -> list[str]:
    """Generate US SSN format: XXX-XX-XXXX (avoids 000, 666, 900-999 area)."""
    results: list[str] = []
    seen: set[str] = set()
    while len(results) < n:
        area = _RNG.randint(1, 665) if _RNG.random() < 0.9 else _RNG.randint(667, 899)
        group = _RNG.randint(1, 99)
        serial = _RNG.randint(1, 9999)
        ssn = f"{area:03d}-{group:02d}-{serial:04d}"
        if ssn not in seen:
            seen.add(ssn)
            results.append(ssn)
    return results


def _cpf_check_digits(nine: list[int]) -> tuple[int, int]:
    """Compute CPF check digits (mod 11 algorithm)."""
    s1 = sum(d * w for d, w in zip(nine, range(10, 1, -1)))
    d1 = 0 if (s1 % 11) < 2 else 11 - (s1 % 11)
    ten = nine + [d1]
    s2 = sum(d * w for d, w in zip(ten, range(11, 1, -1)))
    d2 = 0 if (s2 % 11) < 2 else 11 - (s2 % 11)
    return d1, d2


def _generate_cpfs(n: int) -> list[str]:
    """Generate valid CPF numbers with checksum (XXX.XXX.XXX-XX)."""
    results: list[str] = []
    seen: set[str] = set()
    while len(results) < n:
        nine = [_RNG.randint(0, 9) for _ in range(9)]
        if len(set(nine)) == 1:
            continue
        d1, d2 = _cpf_check_digits(nine)
        digits = nine + [d1, d2]
        cpf = f"{''.join(str(d) for d in digits[:3])}.{''.join(str(d) for d in digits[3:6])}.{''.join(str(d) for d in digits[6:9])}-{''.join(str(d) for d in digits[9:])}"
        if cpf not in seen:
            seen.add(cpf)
            results.append(cpf)
    return results


def _generate_passport_numbers(n: int) -> list[str]:
    """Generate alphanumeric passport numbers in various formats."""
    prefixes = ["A", "B", "C", "E", "G", "K", "L", "M", "N", "P", "S", "T", "U", "X"]
    results: list[str] = []
    seen: set[str] = set()
    while len(results) < n:
        fmt_idx = len(results) % 4
        if fmt_idx == 0:
            val = _RNG.choice(prefixes) + "".join(str(_RNG.randint(0, 9)) for _ in range(8))
        elif fmt_idx == 1:
            val = "".join(_RNG.choice(prefixes) for _ in range(2)) + "".join(str(_RNG.randint(0, 9)) for _ in range(7))
        elif fmt_idx == 2:
            val = _RNG.choice(prefixes) + "".join(str(_RNG.randint(0, 9)) for _ in range(7))
        else:
            val = "".join(str(_RNG.randint(0, 9)) for _ in range(9))
        if val not in seen:
            seen.add(val)
            results.append(val)
    return results


def _generate_drivers_licenses(n: int) -> list[str]:
    """Generate alphanumeric driver's license numbers."""
    results: list[str] = []
    seen: set[str] = set()
    while len(results) < n:
        fmt_idx = len(results) % 5
        if fmt_idx == 0:
            val = _RNG.choice(string.ascii_uppercase) + "".join(str(_RNG.randint(0, 9)) for _ in range(12))
        elif fmt_idx == 1:
            val = "".join(_RNG.choice(string.ascii_uppercase) for _ in range(2)) + "-" + "".join(str(_RNG.randint(0, 9)) for _ in range(8))
        elif fmt_idx == 2:
            val = "DL-" + "".join(str(_RNG.randint(0, 9)) for _ in range(10))
        elif fmt_idx == 3:
            val = "".join(str(_RNG.randint(0, 9)) for _ in range(10))
        else:
            val = _RNG.choice(string.ascii_uppercase) + "".join(str(_RNG.randint(0, 9)) for _ in range(7)) + "-" + "".join(str(_RNG.randint(0, 9)) for _ in range(4))
        if val not in seen:
            seen.add(val)
            results.append(val)
    return results


def _generate_tax_ids(n: int) -> list[str]:
    """Generate numeric tax ID numbers with various digit lengths."""
    results: list[str] = []
    seen: set[str] = set()
    while len(results) < n:
        fmt_idx = len(results) % 4
        if fmt_idx == 0:
            val = "".join(str(_RNG.randint(0, 9)) for _ in range(10))
        elif fmt_idx == 1:
            p1 = "".join(str(_RNG.randint(0, 9)) for _ in range(2))
            p2 = "".join(str(_RNG.randint(0, 9)) for _ in range(7))
            val = f"{p1}-{p2}"
        elif fmt_idx == 2:
            p1 = "".join(str(_RNG.randint(0, 9)) for _ in range(3))
            p2 = "".join(str(_RNG.randint(0, 9)) for _ in range(3))
            p3 = "".join(str(_RNG.randint(0, 9)) for _ in range(3))
            val = f"{p1}-{p2}-{p3}"
        else:
            val = "".join(str(_RNG.randint(0, 9)) for _ in range(12))
        if val not in seen:
            seen.add(val)
            results.append(val)
    return results


def _generate_license_plates(n: int) -> list[str]:
    """Generate license plates in various alphanumeric formats."""
    results: list[str] = []
    seen: set[str] = set()
    while len(results) < n:
        fmt_idx = len(results) % 6
        if fmt_idx == 0:
            val = (f"{''.join(_RNG.choice(string.ascii_uppercase) for _ in range(2))}"
                   f" {''.join(str(_RNG.randint(0, 9)) for _ in range(3))}"
                   f" {''.join(_RNG.choice(string.ascii_uppercase) for _ in range(2))}")
        elif fmt_idx == 1:
            val = (f"{''.join(_RNG.choice(string.ascii_uppercase) for _ in range(3))}"
                   f"-{''.join(str(_RNG.randint(0, 9)) for _ in range(4))}")
        elif fmt_idx == 2:
            val = (f"{''.join(str(_RNG.randint(0, 9)) for _ in range(2))}"
                   f" {''.join(_RNG.choice(string.ascii_uppercase) for _ in range(3))}"
                   f" {''.join(str(_RNG.randint(0, 9)) for _ in range(2))}")
        elif fmt_idx == 3:
            val = (f"{''.join(_RNG.choice(string.ascii_uppercase) for _ in range(2))}"
                   f"{''.join(str(_RNG.randint(0, 9)) for _ in range(4))}"
                   f"{''.join(_RNG.choice(string.ascii_uppercase) for _ in range(2))}")
        elif fmt_idx == 4:
            val = (f"{''.join(str(_RNG.randint(0, 9)) for _ in range(4))}"
                   f" {''.join(_RNG.choice(string.ascii_uppercase) for _ in range(3))}")
        else:
            val = (f"{''.join(_RNG.choice(string.ascii_uppercase) for _ in range(1))}"
                   f"-{''.join(str(_RNG.randint(0, 9)) for _ in range(3))}-"
                   f"{''.join(_RNG.choice(string.ascii_uppercase) for _ in range(2))}")
        if val not in seen:
            seen.add(val)
            results.append(val)
    return results


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

# ALL CAPS person names — tests titlecase normalisation in NER pipeline
PERSON_NAMES_CAPS_EN = [
    "JOHN SMITH", "SARAH JOHNSON", "MICHAEL WILLIAMS", "DAVID BROWN",
    "JENNIFER DAVIS", "ROBERT WILSON", "EMMA THOMPSON", "JAMES ANDERSON",
    "EMILY MARTINEZ", "WILLIAM TAYLOR", "JESSICA MOORE", "DANIEL GARCIA",
    "MARIA LOPEZ", "KEVIN CHEN", "AMANDA WHITE", "THOMAS NGUYEN",
    "RACHEL KIM", "CHRISTOPHER MORGAN", "ELIZABETH CARTER", "MATTHEW LEE",
    "OLIVIA HARRIS", "ANDREW CLARK", "SOPHIA LEWIS", "RYAN WALKER",
    "ISABELLA HALL", "ALEXANDER PETROV", "SOFIA ROSSI", "HANS MUELLER",
    "PRIYA SHARMA", "OLIVER GRANT",
]

PERSON_NAMES_CAPS_TR = [
    "AHMET YILMAZ", "MEHMET DEMİR", "FATMA KAYA", "ALİ ÇELİK",
    "AYŞE ŞAHİN", "MUSTAFA YILDIZ", "ZEYNEP ARSLAN", "HASAN KOÇ",
    "ELİF ÖZTÜRK", "İBRAHİM AYDIN", "SERKAN KARA", "DENİZ YILDIRIM",
    "GÜLŞEN POLAT", "MURAT ERDOĞAN", "EMRE ÖZDEMİR", "SELİN AKTAŞ",
    "ONUR DEMİRTAŞ", "CEREN AKSOY", "TOLGA ŞEN", "BURAK YILMAZ",
    "DERYA KORKMAZ", "HAKAN GÜNEŞ", "ESRA TEKİN", "OĞUZ BAŞARAN",
    "PINAR DOĞAN", "BERK AVCI", "GAMZE ŞİMŞEK", "VOLKAN ÖZER",
    "NESLİHAN AKIN", "CEM KARACA",
]

# Organisation names — hospitals, companies, universities, government
ORGANIZATION_NAMES = [
    "Acme Healthcare Solutions", "Memorial General Hospital",
    "Northern District Medical Center", "Pacific Rim Technologies",
    "Sterling Pharmaceutical Group", "Bright Future Academy",
    "Continental Insurance Partners", "Atlas Global Logistics",
    "Horizon Medical Research Institute", "Summit Financial Advisory",
    "Greenfield Community Hospital", "Oakwood Legal Associates",
    "NextGen Biomedical Labs", "Pinnacle Energy Corporation",
    "Riverside Rehabilitation Center",
]

ORGANIZATION_NAMES_TR = [
    "Anadolu Sağlık Merkezi", "İstanbul Üniversitesi Hastanesi",
    "Boğaziçi Teknoloji Araştırma Enstitüsü", "Marmara Sigorta Şirketi",
    "Akdeniz Tıp Laboratuvarı", "Karadeniz Enerji Holding",
    "Trakya Hukuk Bürosu", "Ege Lojistik Çözümleri",
    "Başkent İlaç Sanayi", "Kızılay Kan Merkezi",
    "Gazi Üniversitesi Araştırma Hastanesi", "Ankara Eğitim Vakfı",
    "Doğu Akdeniz Finans Grubu", "Yıldız Teknik Danışmanlık",
    "Güneydoğu Tarım Kooperatifi",
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

# --- Multilingual contexts for extended Presidio entity types ---

_CTX_DATE_OF_BIRTH = [
    # en
    "Date of birth: {v}.", "Born on {v}.", "DOB {v} on file.",
    # tr
    "Do\u011fum tarihi: {v}.", "Do\u011fum: {v}.",
    # de
    "Geburtsdatum: {v}.", "Geboren am {v}.",
    # fr
    "Date de naissance : {v}.", "N\u00e9(e) le {v}.",
    # es
    "Fecha de nacimiento: {v}.", "Nacido el {v}.",
    # pt
    "Data de nascimento: {v}.", "Nascido em {v}.",
    # ar
    "\u062a\u0627\u0631\u064a\u062e \u0627\u0644\u0645\u064a\u0644\u0627\u062f: {v}.",
    # zh
    "\u51fa\u751f\u65e5\u671f\uff1a{v}\u3002",
]
_CTX_MAC_ADDRESS = [
    # en
    "MAC address {v} registered.", "Device with MAC {v} detected.",
    "Network adapter: {v}.",
    # tr
    "MAC adresi {v} kay\u0131tl\u0131.", "Cihaz MAC: {v}.",
    # de
    "MAC-Adresse {v} registriert.", "Netzwerkadapter: {v}.",
    # fr
    "Adresse MAC {v} enregistr\u00e9e.", "Adaptateur r\u00e9seau : {v}.",
    # es
    "Direcci\u00f3n MAC {v} registrada.", "Adaptador de red: {v}.",
    # pt
    "Endere\u00e7o MAC {v} registrado.", "Adaptador de rede: {v}.",
    # ar
    "\u0639\u0646\u0648\u0627\u0646 MAC: {v}.",
    # zh
    "MAC\u5730\u5740\uff1a{v}\u3002",
]
_CTX_URL = [
    # en
    "Visit {v} for details.", "API endpoint: {v}.",
    "Documentation at {v}.",
    # tr
    "Detaylar i\u00e7in {v} adresini ziyaret edin.", "API adresi: {v}.",
    # de
    "Weitere Informationen unter {v}.", "API-Endpunkt: {v}.",
    # fr
    "Consultez {v} pour plus de d\u00e9tails.", "Point d'acc\u00e8s API : {v}.",
    # es
    "Visite {v} para m\u00e1s informaci\u00f3n.", "Punto de acceso API: {v}.",
    # pt
    "Acesse {v} para mais detalhes.", "Endpoint da API: {v}.",
    # ar
    "\u0642\u0645 \u0628\u0632\u064a\u0627\u0631\u0629 {v} \u0644\u0644\u062a\u0641\u0627\u0635\u064a\u0644.",
    # zh
    "\u8bf7\u8bbf\u95ee{v}\u4e86\u89e3\u8be6\u60c5\u3002",
]
_CTX_COORDINATES = [
    # en
    "GPS coordinates: {v}.", "Location pinpointed at {v}.",
    "Geolocation: {v}.",
    # tr
    "GPS koordinatlar\u0131: {v}.", "Konum: {v}.",
    # de
    "GPS-Koordinaten: {v}.", "Standort: {v}.",
    # fr
    "Coordonn\u00e9es GPS : {v}.", "Localisation : {v}.",
    # es
    "Coordenadas GPS: {v}.", "Ubicaci\u00f3n: {v}.",
    # pt
    "Coordenadas GPS: {v}.", "Localiza\u00e7\u00e3o: {v}.",
    # ar
    "\u0625\u062d\u062f\u0627\u062b\u064a\u0627\u062a GPS: {v}.",
    # zh
    "GPS\u5750\u6807\uff1a{v}\u3002",
]
_CTX_COOKIE_ID = [
    # en
    "Tracking cookie: {v}.", "Session cookie {v} active.",
    "Cookie identifier: {v}.",
    # tr
    "\u0130zleme \u00e7erezi: {v}.", "Oturum \u00e7erezi: {v}.",
    # de
    "Tracking-Cookie: {v}.", "Sitzungscookie: {v}.",
    # fr
    "Cookie de suivi : {v}.", "Cookie de session : {v}.",
    # es
    "Cookie de seguimiento: {v}.", "Cookie de sesi\u00f3n: {v}.",
    # pt
    "Cookie de rastreamento: {v}.", "Cookie de sess\u00e3o: {v}.",
    # ar
    "\u0645\u0639\u0631\u0641 \u0627\u0644\u0643\u0648\u0643\u064a: {v}.",
    # zh
    "\u8ddf\u8e2aCookie\uff1a{v}\u3002",
]
_CTX_DEVICE_ID = [
    # en
    "Device identifier: {v}.", "IMEI: {v}.",
    "Registered device {v}.",
    # tr
    "Cihaz kimli\u011fi: {v}.", "IMEI: {v}.",
    # de
    "Ger\u00e4tekennung: {v}.", "IMEI: {v}.",
    # fr
    "Identifiant de l'appareil : {v}.", "IMEI : {v}.",
    # es
    "Identificador del dispositivo: {v}.", "IMEI: {v}.",
    # pt
    "Identificador do dispositivo: {v}.", "IMEI: {v}.",
    # ar
    "\u0645\u0639\u0631\u0641 \u0627\u0644\u062c\u0647\u0627\u0632: {v}.",
    # zh
    "\u8bbe\u5907\u6807\u8bc6\u7b26\uff1a{v}\u3002",
]
_CTX_SSN = [
    # en
    "Social Security Number: {v}.", "SSN {v} on record.",
    "Social security: {v}.",
    # tr
    "Sosyal g\u00fcvenlik numaras\u0131: {v}.", "SGK no: {v}.",
    # de
    "Sozialversicherungsnummer: {v}.", "SVN: {v}.",
    # fr
    "Num\u00e9ro de s\u00e9curit\u00e9 sociale : {v}.", "NSS : {v}.",
    # es
    "N\u00famero de seguro social: {v}.", "NSS: {v}.",
    # pt
    "N\u00famero de seguro social: {v}.", "INSS: {v}.",
    # ar
    "\u0631\u0642\u0645 \u0627\u0644\u0636\u0645\u0627\u0646 \u0627\u0644\u0627\u062c\u062a\u0645\u0627\u0639\u064a: {v}.",
    # zh
    "\u793e\u4f1a\u4fdd\u969c\u53f7\u7801\uff1a{v}\u3002",
]
_CTX_CPF = [
    # en
    "CPF number: {v}.", "Tax registration: {v}.",
    "Individual taxpayer ID: {v}.",
    # tr
    "Vergi kimli\u011fi: {v}.", "M\u00fckellef no: {v}.",
    # de
    "Steuernummer: {v}.", "Steuer-ID: {v}.",
    # fr
    "Num\u00e9ro CPF : {v}.", "Identifiant fiscal : {v}.",
    # es
    "N\u00famero CPF: {v}.", "Registro fiscal: {v}.",
    # pt
    "CPF: {v}.", "Cadastro de pessoa f\u00edsica: {v}.",
    # ar
    "\u0631\u0642\u0645 \u0627\u0644\u062a\u0633\u062c\u064a\u0644 \u0627\u0644\u0636\u0631\u064a\u0628\u064a: {v}.",
    # zh
    "CPF\u53f7\u7801\uff1a{v}\u3002",
]
_CTX_PASSPORT = [
    # en
    "Passport number: {v}.", "Travel document: {v}.",
    "Passport {v} issued.",
    # tr
    "Pasaport numaras\u0131: {v}.", "Seyahat belgesi: {v}.",
    # de
    "Reisepassnummer: {v}.", "Reisedokument: {v}.",
    # fr
    "Num\u00e9ro de passeport : {v}.", "Document de voyage : {v}.",
    # es
    "N\u00famero de pasaporte: {v}.", "Documento de viaje: {v}.",
    # pt
    "N\u00famero do passaporte: {v}.", "Documento de viagem: {v}.",
    # ar
    "\u0631\u0642\u0645 \u062c\u0648\u0627\u0632 \u0627\u0644\u0633\u0641\u0631: {v}.",
    # zh
    "\u62a4\u7167\u53f7\u7801\uff1a{v}\u3002",
]
_CTX_DRIVERS_LICENSE = [
    # en
    "Driver's license: {v}.", "License number: {v}.",
    "Driving permit {v} valid.",
    # tr
    "Ehliyet numaras\u0131: {v}.", "S\u00fcr\u00fcc\u00fc belgesi: {v}.",
    # de
    "F\u00fchrerscheinnummer: {v}.", "Fahrerlaubnis: {v}.",
    # fr
    "Num\u00e9ro de permis de conduire : {v}.", "Permis : {v}.",
    # es
    "N\u00famero de licencia de conducir: {v}.", "Permiso: {v}.",
    # pt
    "N\u00famero da carteira de motorista: {v}.", "Habilita\u00e7\u00e3o: {v}.",
    # ar
    "\u0631\u0642\u0645 \u0631\u062e\u0635\u0629 \u0627\u0644\u0642\u064a\u0627\u062f\u0629: {v}.",
    # zh
    "\u9a7e\u7167\u53f7\u7801\uff1a{v}\u3002",
]
_CTX_TAX_ID = [
    # en
    "Tax identification number: {v}.", "TIN: {v}.",
    "Tax ID {v} on file.",
    # tr
    "Vergi numaras\u0131: {v}.", "VKN: {v}.",
    # de
    "Steueridentifikationsnummer: {v}.", "Steuer-ID: {v}.",
    # fr
    "Num\u00e9ro d'identification fiscale : {v}.", "NIF : {v}.",
    # es
    "N\u00famero de identificaci\u00f3n fiscal: {v}.", "NIF: {v}.",
    # pt
    "N\u00famero de identifica\u00e7\u00e3o fiscal: {v}.", "NIF: {v}.",
    # ar
    "\u0631\u0642\u0645 \u0627\u0644\u062a\u0639\u0631\u064a\u0641 \u0627\u0644\u0636\u0631\u064a\u0628\u064a: {v}.",
    # zh
    "\u7a0e\u52a1\u8bc6\u522b\u53f7\uff1a{v}\u3002",
]
_CTX_LICENSE_PLATE = [
    # en
    "License plate: {v}.", "Vehicle registration: {v}.",
    "Plate number {v} recorded.",
    # tr
    "Plaka: {v}.", "Ara\u00e7 tescil: {v}.",
    # de
    "Kennzeichen: {v}.", "Fahrzeugregistrierung: {v}.",
    # fr
    "Plaque d'immatriculation : {v}.", "Immatriculation : {v}.",
    # es
    "Matr\u00edcula: {v}.", "Registro del veh\u00edculo: {v}.",
    # pt
    "Placa do ve\u00edculo: {v}.", "Registro do ve\u00edculo: {v}.",
    # ar
    "\u0644\u0648\u062d\u0629 \u0627\u0644\u0633\u064a\u0627\u0631\u0629: {v}.",
    # zh
    "\u8f66\u724c\u53f7\uff1a{v}\u3002",
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
# ALL CAPS person name contexts — official/medical document style
_CTX_NAME_CAPS_EN = [
    "PATIENT: {v} — Lab results attached.", "SUBJECT: {v} — Review pending.",
    "APPLICANT: {v} — Documents received.", "EMPLOYEE: {v} — Annual review.",
    "File opened for {v}.", "Record updated for {v}.",
    "Certification issued to {v}.", "Case assigned to {v}.",
    "INSURED: {v} — Policy active.", "BENEFICIARY: {v} — Claim submitted.",
]
_CTX_NAME_CAPS_TR = [
    "HASTA: {v} — Tahlil sonuçları ektedir.", "BAŞVURU SAHİBİ: {v} — Belgeler alındı.",
    "ÇALIŞAN: {v} — Yıllık değerlendirme.", "SİGORTALI: {v} — Poliçe aktif.",
    "{v} için dosya açılmıştır.", "Kayıt {v} için güncellenmiştir.",
    "Sertifika {v} adına düzenlenmiştir.", "Dava {v} adına atanmıştır.",
    "HASTA ADI: {v} — Muayene sonuçları.", "ALICI: {v} — Talep iletildi.",
]
# Organization name contexts
_CTX_ORG_EN = [
    "The audit was conducted at {v}.", "{v} submitted a compliance report.",
    "Documents received from {v}.", "Contract signed with {v}.",
    "{v} appointed a new director.", "Annual report published by {v}.",
    "Inspection completed at {v}.", "Partnership agreement with {v}.",
    "Grant awarded to {v}.", "Referral received from {v}.",
]
_CTX_ORG_TR = [
    "Denetim {v} bünyesinde gerçekleştirildi.", "{v} uyumluluk raporu sundu.",
    "Belgeler {v} tarafından iletildi.", "{v} ile sözleşme imzalandı.",
    "{v} yeni bir müdür atadı.", "Yıllık rapor {v} tarafından yayımlandı.",
    "İnceleme {v} bünyesinde tamamlandı.", "{v} ile ortaklık anlaşması yapıldı.",
    "Hibe {v} bünyesine aktarıldı.", "Sevk {v} tarafından alındı.",
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
    + _build_docs(PERSON_NAMES_CAPS_EN, "PERSON_NAME", "ner_name_caps_en", _CTX_NAME_CAPS_EN, "en", 5)
    + _build_docs(PERSON_NAMES_CAPS_TR, "PERSON_NAME", "ner_name_caps_tr", _CTX_NAME_CAPS_TR, "tr", 5)
    + _build_docs(LOCATIONS_EN, "LOCATION", "ner_loc_en", _CTX_LOC_EN, "en", 5)
    + _build_docs(LOCATIONS_TR, "LOCATION", "ner_loc_tr", _CTX_LOC_TR, "tr", 5)
    + _build_docs(ORGANIZATION_NAMES, "ORGANIZATION_NAME", "ner_org_en", _CTX_ORG_EN, "en", 5)
    + _build_docs(ORGANIZATION_NAMES_TR, "ORGANIZATION_NAME", "ner_org_tr", _CTX_ORG_TR, "tr", 5)
)

# ═══════════════════════════════════════════════════════════════════════════
#  ADVERSARIAL PRESIDIO — real-world edge cases
# ═══════════════════════════════════════════════════════════════════════════

def _generate_spaced_ibans(n: int) -> list[str]:
    """IBANs with spaces as printed on bank statements."""
    raw = _generate_ibans(n)
    spaced: list[str] = []
    for iban in raw:
        # Insert spaces every 4 characters: DE89 3704 0044 0532 0130 00
        spaced.append(" ".join(iban[i:i+4] for i in range(0, len(iban), 4)))
    return spaced


def _generate_dotted_phones(n: int) -> list[str]:
    """Phones with dots instead of spaces: +90.532.123.45.67"""
    country_codes = ["90", "44", "1", "49", "33", "34", "39", "31"]
    phones: list[str] = []
    while len(phones) < n:
        cc = country_codes[len(phones) % len(country_codes)]
        d = [str(_RNG.randint(0, 9)) for _ in range(10)]
        phone = f"+{cc}.{''.join(d[0:3])}.{''.join(d[3:6])}.{''.join(d[6:8])}.{''.join(d[8:10])}"
        phones.append(phone)
    return phones


def _generate_paren_phones(n: int) -> list[str]:
    """Phones with parentheses: +1 (555) 123-4567"""
    country_codes = ["1", "44", "49", "33", "90", "34", "61", "81"]
    phones: list[str] = []
    while len(phones) < n:
        cc = country_codes[len(phones) % len(country_codes)]
        d = [str(_RNG.randint(0, 9)) for _ in range(10)]
        phone = f"+{cc} ({''.join(d[0:3])}) {''.join(d[3:6])}-{''.join(d[6:10])}"
        phones.append(phone)
    return phones


def _generate_dashed_credit_cards(n: int) -> list[str]:
    """Credit cards with dashes: 4532-1234-5678-9012"""
    raw = _generate_credit_cards(n)
    return [f"{c[:4]}-{c[4:8]}-{c[8:12]}-{c[12:]}" if len(c) == 16
            else f"{c[:4]}-{c[4:10]}-{c[10:]}" for c in raw]


def _generate_spaced_tckns(n: int) -> list[str]:
    """TCKNs with spaces every 3 digits: 123 456 789 01"""
    raw = _generate_tckns(n)
    return [f"{t[:3]} {t[3:6]} {t[6:9]} {t[9:]}" for t in raw]


_N_ADV = 30  # adversarial samples per sub-type

ADVERSARIAL_DOCUMENTS: list[BenchmarkDocument] = (
    _build_docs(_generate_spaced_ibans(_N_ADV), "IBAN", "adv_iban_spaced",
                ["Wire transfer to {v}.", "IBAN: {v} — verified.", "Account {v} active."], "en", 5)
    + _build_docs(_generate_dotted_phones(_N_ADV), "PHONE_NUMBER", "adv_phone_dotted",
                  ["Reach us at {v}.", "Phone: {v}.", "Contact {v}."], "en", 5)
    + _build_docs(_generate_paren_phones(_N_ADV), "PHONE_NUMBER", "adv_phone_paren",
                  ["Call {v} for info.", "Office: {v}.", "Dial {v}."], "en", 5)
    + _build_docs(_generate_dashed_credit_cards(_N_ADV), "CREDIT_CARD_NUMBER", "adv_cc_dashed",
                  ["Card {v} charged.", "Payment on {v}.", "Refund to {v}."], "en", 5)
    + _build_docs(_generate_spaced_tckns(_N_ADV), "NATIONAL_ID", "adv_tckn_spaced",
                  ["TC kimlik: {v}.", "Kayıt {v}.", "Dosya: {v}."], "tr", 5)
)


# ═══════════════════════════════════════════════════════════════════════════
#  EXTENDED PRESIDIO — new pattern-based entity types (30 values each)
# ═══════════════════════════════════════════════════════════════════════════

_N_EXT = 30

EXTENDED_PRESIDIO_DOCUMENTS: list[BenchmarkDocument] = (
    _build_docs(_generate_dates_of_birth(_N_EXT), "DATE_OF_BIRTH", "dob",
                _CTX_DATE_OF_BIRTH, "en", 5)
    + _build_docs(_generate_mac_addresses(_N_EXT), "MAC_ADDRESS", "mac",
                  _CTX_MAC_ADDRESS, "en", 5)
    + _build_docs(_generate_urls(_N_EXT), "URL", "url",
                  _CTX_URL, "en", 5)
    + _build_docs(_generate_coordinates(_N_EXT), "COORDINATES", "coords",
                  _CTX_COORDINATES, "en", 5)
    + _build_docs(_generate_cookie_ids(_N_EXT), "COOKIE_ID", "cookie",
                  _CTX_COOKIE_ID, "en", 5)
    + _build_docs(_generate_device_ids(_N_EXT), "DEVICE_ID", "device",
                  _CTX_DEVICE_ID, "en", 5)
    + _build_docs(_generate_ssns(_N_EXT), "SOCIAL_SECURITY_NUMBER", "ssn",
                  _CTX_SSN, "en", 5)
    + _build_docs(_generate_cpfs(_N_EXT), "CPF", "cpf",
                  _CTX_CPF, "en", 5)
    + _build_docs(_generate_passport_numbers(_N_EXT), "PASSPORT_NUMBER", "passport",
                  _CTX_PASSPORT, "en", 5)
    + _build_docs(_generate_drivers_licenses(_N_EXT), "DRIVERS_LICENSE", "drivers_lic",
                  _CTX_DRIVERS_LICENSE, "en", 5)
    + _build_docs(_generate_tax_ids(_N_EXT), "TAX_ID", "tax_id",
                  _CTX_TAX_ID, "en", 5)
    + _build_docs(_generate_license_plates(_N_EXT), "LICENSE_PLATE", "plate",
                  _CTX_LICENSE_PLATE, "en", 5)
)


# ═══════════════════════════════════════════════════════════════════════════
#  MULTILINGUAL NER — 8 additional languages
# ═══════════════════════════════════════════════════════════════════════════

PERSON_NAMES_DE = [
    "Hans Müller", "Anna Schmidt", "Klaus Weber", "Petra Fischer",
    "Wolfgang Becker", "Sabine Schulz", "Jürgen Hoffmann", "Monika Richter",
    "Thomas Zimmermann", "Ingrid Braun", "Stefan Krüger", "Birgit Lange",
    "Michael Hartmann", "Claudia Werner", "Andreas Schwarz", "Renate Koch",
    "Markus Schmitt", "Heike Neumann", "Frank Lehmann", "Eva König",
]

PERSON_NAMES_FR = [
    "Jean Dupont", "Marie Laurent", "Pierre Martin", "Isabelle Bernard",
    "François Moreau", "Catherine Petit", "Nicolas Durand", "Sophie Leroy",
    "Philippe Roux", "Nathalie Fournier", "Laurent Girard", "Véronique Bonnet",
    "Alain Mercier", "Sylvie Lambert", "Patrick Fontaine", "Anne Rousseau",
    "Christophe Blanc", "Martine Guérin", "Olivier Faure", "Brigitte Chevalier",
]

PERSON_NAMES_ES = [
    "Carlos García", "María López", "Juan Martínez", "Ana Rodríguez",
    "Pedro Hernández", "Laura Sánchez", "Miguel Pérez", "Carmen Gómez",
    "José Fernández", "Isabel Díaz", "Antonio Torres", "Rosa Ruiz",
    "Francisco Moreno", "Elena Muñoz", "Rafael Álvarez", "Lucía Romero",
    "Manuel Navarro", "Pilar Domínguez", "Diego Vázquez", "Teresa Ramos",
]

PERSON_NAMES_PT = [
    "João Silva", "Ana Santos", "Pedro Oliveira", "Maria Pereira",
    "Carlos Ferreira", "Mariana Costa", "Luís Rodrigues", "Sofia Almeida",
    "António Martins", "Teresa Araújo", "Ricardo Sousa", "Inês Fernandes",
    "José Gonçalves", "Catarina Gomes", "Paulo Ribeiro", "Beatriz Lopes",
    "Rui Marques", "Marta Teixeira", "Miguel Carvalho", "Helena Correia",
]

PERSON_NAMES_IT = [
    "Marco Rossi", "Giulia Russo", "Luca Bianchi", "Francesca Romano",
    "Alessandro Ferrari", "Maria Colombo", "Giuseppe Ricci", "Chiara Marino",
    "Andrea Greco", "Sara Gallo", "Matteo Bruno", "Valentina Conti",
    "Davide Costa", "Elisa De Luca", "Simone Mancini", "Laura Giordano",
    "Federico Lombardi", "Anna Moretti", "Roberto Barbieri", "Elena Fontana",
]

PERSON_NAMES_NL = [
    "Jan de Vries", "Maria van Dijk", "Pieter Bakker", "Anna Janssen",
    "Willem de Boer", "Sophie Visser", "Dirk Smit", "Emma Meijer",
    "Hendrik de Groot", "Lisa Bos", "Kees Mulder", "Eva Peters",
    "Maarten Hendriks", "Laura Dekker", "Thomas van den Berg", "Sara Koning",
    "Joost Dijkstra", "Lotte Vermeer", "Bart Schouten", "Anne van Leeuwen",
]

PERSON_NAMES_PL = [
    "Jan Kowalski", "Anna Nowak", "Piotr Wiśniewski", "Maria Wójcik",
    "Tomasz Kamiński", "Katarzyna Lewandowska", "Andrzej Zieliński", "Agnieszka Szymańska",
    "Krzysztof Woźniak", "Magdalena Dąbrowska", "Marek Kozłowski", "Joanna Jankowska",
    "Paweł Mazur", "Barbara Wojciechowska", "Michał Kwiatkowski", "Monika Krawczyk",
    "Adam Piotrowski", "Ewa Grabowska", "Stanisław Pawlak", "Dorota Michalska",
]

PERSON_NAMES_RU = [
    "Иван Иванов", "Мария Петрова", "Алексей Сидоров", "Елена Козлова",
    "Дмитрий Волков", "Анна Новикова", "Сергей Морозов", "Ольга Соколова",
    "Андрей Лебедев", "Наталья Попова", "Михаил Кузнецов", "Татьяна Смирнова",
    "Николай Орлов", "Екатерина Федорова", "Владимир Степанов", "Светлана Павлова",
    "Александр Белов", "Ирина Захарова", "Максим Васильев", "Юлия Николаева",
]

# --- Multilingual locations ---

LOCATIONS_DE = [
    "Berlin", "München", "Hamburg", "Frankfurt", "Köln",
    "Stuttgart", "Düsseldorf", "Dresden", "Leipzig", "Hannover",
    "Nürnberg", "Bremen", "Heidelberg", "Freiburg", "Bonn",
]

LOCATIONS_FR = [
    "Paris", "Lyon", "Marseille", "Toulouse", "Bordeaux",
    "Strasbourg", "Nantes", "Montpellier", "Lille", "Nice",
    "Grenoble", "Rennes", "Dijon", "Rouen", "Toulon",
]

LOCATIONS_ES = [
    "Madrid", "Barcelona", "Sevilla", "Valencia", "Bilbao",
    "Málaga", "Zaragoza", "Granada", "Córdoba", "Salamanca",
    "Toledo", "Murcia", "Palma", "Alicante", "Valladolid",
]

LOCATIONS_PT = [
    "Lisboa", "Porto", "Coimbra", "Braga", "Faro",
    "Funchal", "Aveiro", "Évora", "Setúbal", "Viseu",
    "Guimarães", "Leiria", "Cascais", "Sintra", "Tavira",
]

LOCATIONS_IT = [
    "Roma", "Milano", "Napoli", "Torino", "Firenze",
    "Bologna", "Venezia", "Genova", "Palermo", "Verona",
    "Catania", "Bari", "Trieste", "Padova", "Perugia",
]

LOCATIONS_NL = [
    "Amsterdam", "Rotterdam", "Utrecht", "Den Haag", "Eindhoven",
    "Groningen", "Maastricht", "Leiden", "Haarlem", "Delft",
    "Nijmegen", "Tilburg", "Breda", "Almere", "Arnhem",
]

LOCATIONS_PL = [
    "Warszawa", "Kraków", "Wrocław", "Poznań", "Gdańsk",
    "Łódź", "Szczecin", "Lublin", "Katowice", "Białystok",
    "Toruń", "Rzeszów", "Olsztyn", "Opole", "Kielce",
]

LOCATIONS_RU = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
    "Нижний Новгород", "Челябинск", "Самара", "Ростов-на-Дону", "Уфа",
    "Красноярск", "Пермь", "Воронеж", "Волгоград", "Краснодар",
]

# --- Multilingual context templates ---

_CTX_NAME_DE = [
    "Der Bericht wurde von {v} erstellt.", "{v} hat an der Sitzung teilgenommen.",
    "Die Vereinbarung wurde von {v} unterzeichnet.", "{v} hat den Vorschlag eingereicht.",
    "Die Prüfung wurde von {v} durchgeführt.", "{v} übernimmt die Projektleitung.",
    "Die Schulung wurde von {v} organisiert.", "{v} hat den Vertrag genehmigt.",
    "Die Bewertung wurde von {v} abgegeben.", "{v} hat den Vorsitz geführt.",
]
_CTX_NAME_FR = [
    "Le rapport a été préparé par {v}.", "{v} a assisté à la réunion.",
    "Le contrat a été signé par {v}.", "{v} a soumis la proposition budgétaire.",
    "L'audit a été réalisé par {v}.", "{v} dirigera l'initiative.",
    "La formation a été organisée par {v}.", "{v} a approuvé le contrat.",
    "L'évaluation a été rendue par {v}.", "{v} a présidé la séance.",
]
_CTX_NAME_ES = [
    "El informe fue preparado por {v}.", "{v} asistió a la reunión.",
    "El contrato fue firmado por {v}.", "{v} presentó la propuesta presupuestaria.",
    "La auditoría fue realizada por {v}.", "{v} liderará la iniciativa.",
    "La formación fue organizada por {v}.", "{v} aprobó el contrato.",
    "La evaluación fue entregada por {v}.", "{v} presidió la sesión.",
]
_CTX_NAME_PT = [
    "O relatório foi preparado por {v}.", "{v} participou da reunião.",
    "O contrato foi assinado por {v}.", "{v} apresentou a proposta orçamentária.",
    "A auditoria foi realizada por {v}.", "{v} liderará a iniciativa.",
    "O treinamento foi organizado por {v}.", "{v} aprovou o contrato.",
    "A avaliação foi entregue por {v}.", "{v} presidiu a sessão.",
]
_CTX_NAME_IT = [
    "Il rapporto è stato preparato da {v}.", "{v} ha partecipato alla riunione.",
    "Il contratto è stato firmato da {v}.", "{v} ha presentato la proposta.",
    "L'audit è stato condotto da {v}.", "{v} guiderà l'iniziativa.",
    "La formazione è stata organizzata da {v}.", "{v} ha approvato il contratto.",
    "La valutazione è stata consegnata da {v}.", "{v} ha presieduto la sessione.",
]
_CTX_NAME_NL = [
    "Het rapport is opgesteld door {v}.", "{v} was aanwezig bij de vergadering.",
    "Het contract is ondertekend door {v}.", "{v} heeft het voorstel ingediend.",
    "De audit is uitgevoerd door {v}.", "{v} zal het initiatief leiden.",
    "De training is georganiseerd door {v}.", "{v} heeft het contract goedgekeurd.",
    "De beoordeling is afgeleverd door {v}.", "{v} heeft de vergadering voorgezeten.",
]
_CTX_NAME_PL = [
    "Raport został przygotowany przez {v}.", "{v} uczestniczył w spotkaniu.",
    "Umowa została podpisana przez {v}.", "{v} przedstawił propozycję budżetu.",
    "Audyt został przeprowadzony przez {v}.", "{v} poprowadzi inicjatywę.",
    "Szkolenie zostało zorganizowane przez {v}.", "{v} zatwierdził umowę.",
    "Ocena została dostarczona przez {v}.", "{v} przewodniczył sesji.",
]
_CTX_NAME_RU = [
    "Отчёт подготовлен {v}.", "{v} присутствовал на совещании.",
    "Договор подписан {v}.", "{v} представил бюджетное предложение.",
    "Аудит проведён {v}.", "{v} возглавит инициативу.",
    "Обучение организовано {v}.", "{v} утвердил контракт.",
    "Оценка представлена {v}.", "{v} председательствовал на заседании.",
]

_CTX_LOC_DE = [
    "Die Konferenz findet in {v} statt.", "Unser Büro in {v} wächst.",
    "Die Sendung wurde aus {v} versandt.", "In {v} werden die Geschäfte ausgebaut.",
    "Eine neue Filiale wurde in {v} eröffnet.",
]
_CTX_LOC_FR = [
    "La conférence se tiendra à {v}.", "Notre bureau à {v} est en croissance.",
    "L'envoi a été expédié de {v}.", "Les opérations à {v} se développent.",
    "Une nouvelle filiale a ouvert à {v}.",
]
_CTX_LOC_ES = [
    "La conferencia se celebrará en {v}.", "Nuestra oficina en {v} crece.",
    "El envío fue despachado desde {v}.", "Las operaciones en {v} se expanden.",
    "Se abrió una nueva sucursal en {v}.",
]
_CTX_LOC_PT = [
    "A conferência será realizada em {v}.", "O nosso escritório em {v} está crescendo.",
    "A remessa foi enviada de {v}.", "As operações em {v} estão expandindo.",
    "Uma nova filial foi aberta em {v}.",
]
_CTX_LOC_IT = [
    "La conferenza si terrà a {v}.", "Il nostro ufficio a {v} è in crescita.",
    "La spedizione è stata inviata da {v}.", "Le operazioni a {v} si espandono.",
    "Una nuova filiale è stata aperta a {v}.",
]
_CTX_LOC_NL = [
    "De conferentie wordt gehouden in {v}.", "Ons kantoor in {v} groeit.",
    "De zending is verstuurd vanuit {v}.", "De activiteiten in {v} breiden zich uit.",
    "Een nieuw filiaal is geopend in {v}.",
]
_CTX_LOC_PL = [
    "Konferencja odbędzie się w {v}.", "Nasze biuro w {v} się rozwija.",
    "Przesyłka została wysłana z {v}.", "Operacje w {v} się rozszerzają.",
    "Nowy oddział został otwarty w {v}.",
]
_CTX_LOC_RU = [
    "Конференция пройдёт в {v}.", "Наш офис в {v} растёт.",
    "Груз отправлен из {v}.", "Операции в {v} расширяются.",
    "Новый филиал открыт в {v}.",
]

# --- ALL CAPS multilingual ---

PERSON_NAMES_CAPS_DE = [n.upper() for n in PERSON_NAMES_DE[:15]]
PERSON_NAMES_CAPS_FR = [n.upper() for n in PERSON_NAMES_FR[:15]]
PERSON_NAMES_CAPS_ES = [n.upper() for n in PERSON_NAMES_ES[:15]]
PERSON_NAMES_CAPS_RU = [n.upper() for n in PERSON_NAMES_RU[:15]]

# --- Non-Latin script names ---

PERSON_NAMES_AR = [
    "محمد أحمد", "فاطمة علي", "عبدالله حسن", "نورة السعيد",
    "خالد العمري", "مريم إبراهيم", "سعود الدوسري", "هند الشمري",
    "ياسر القحطاني", "سارة الحربي", "عمر الغامدي", "لينا المطيري",
    "راشد الزهراني", "أمل العتيبي", "بدر الشهري", "ريم المالكي",
    "سلطان البلوي", "دانة الرشيدي", "فهد الجهني", "نوف السبيعي",
]

PERSON_NAMES_ZH = [
    "王伟", "李娜", "张强", "刘芳", "陈明",
    "杨秀英", "赵军", "黄丽", "周杰", "吴敏",
    "徐静", "孙磊", "马红", "朱鹏", "胡雪",
    "郭强", "何琳", "林涛", "罗玲", "梁宇",
]

PERSON_NAMES_JA = [
    "田中太郎", "佐藤花子", "鈴木一郎", "高橋美咲",
    "伊藤健太", "渡辺裕子", "山本大輔", "中村真理",
    "小林翔太", "加藤由美", "吉田直人", "山田恵子",
    "松本隆", "井上智子", "木村拓也", "林美穂",
    "斎藤誠", "清水幸子", "山口大地", "阪本愛",
]

PERSON_NAMES_HI = [
    "राहुल शर्मा", "प्रिया गुप्ता", "अमित कुमार", "नेहा सिंह",
    "विकास पटेल", "सुनीता यादव", "राजेश वर्मा", "अंजलि जोशी",
    "संजय मिश्रा", "पूजा अग्रवाल", "मनोज तिवारी", "रीना दुबे",
    "अजय चौहान", "मीना राजपूत", "सुरेश पांडेय", "कविता दीक्षित",
    "दीपक सक्सेना", "रश्मि त्रिपाठी", "अनिल श्रीवास्तव", "स्मिता नायर",
]

LOCATIONS_AR = [
    "الرياض", "جدة", "مكة المكرمة", "المدينة المنورة", "الدمام",
    "القاهرة", "الإسكندرية", "دبي", "أبوظبي", "الدوحة",
    "الكويت", "البحرين", "مسقط", "عمّان", "بيروت",
]

LOCATIONS_ZH = [
    "北京", "上海", "广州", "深圳", "杭州",
    "成都", "南京", "武汉", "重庆", "西安",
    "苏州", "天津", "青岛", "大连", "厦门",
]

LOCATIONS_JA = [
    "東京", "大阪", "京都", "横浜", "名古屋",
    "神戸", "福岡", "札幌", "仙台", "広島",
    "奈良", "金沢", "長崎", "鹿児島", "沖縄",
]

LOCATIONS_HI = [
    "मुंबई", "दिल्ली", "बैंगलोर", "चेन्नई", "कोलकाता",
    "हैदराबाद", "पुणे", "अहमदाबाद", "जयपुर", "लखनऊ",
    "वाराणसी", "आगरा", "भोपाल", "इंदौर", "चंडीगढ़",
]

_CTX_NAME_AR = [
    "تم إعداد التقرير بواسطة {v}.", "حضر {v} الاجتماع.",
    "تم توقيع العقد من قبل {v}.", "قدم {v} المقترح.",
    "أجرى {v} التدقيق.", "{v} سيقود المبادرة.",
    "نظم {v} التدريب.", "وافق {v} على العقد.",
    "قدم {v} التقييم.", "ترأس {v} الجلسة.",
]
_CTX_NAME_ZH = [
    "报告由{v}准备。", "{v}出席了会议。",
    "合同由{v}签署。", "{v}提交了预算提案。",
    "审计由{v}进行。", "{v}将领导该计划。",
    "培训由{v}组织。", "{v}批准了合同。",
    "评估由{v}提交。", "{v}主持了会议。",
]
_CTX_NAME_JA = [
    "報告書は{v}が作成しました。", "{v}は会議に出席しました。",
    "契約は{v}が署名しました。", "{v}は予算案を提出しました。",
    "監査は{v}が実施しました。", "{v}がイニシアチブを率います。",
    "研修は{v}が企画しました。", "{v}は契約を承認しました。",
    "評価は{v}が提出しました。", "{v}は会議の議長を務めました。",
]
_CTX_NAME_HI = [
    "रिपोर्ट {v} द्वारा तैयार की गई।", "{v} ने बैठक में भाग लिया।",
    "अनुबंध {v} द्वारा हस्ताक्षरित किया गया।", "{v} ने बजट प्रस्ताव प्रस्तुत किया।",
    "ऑडिट {v} द्वारा किया गया।", "{v} पहल का नेतृत्व करेंगे।",
    "प्रशिक्षण {v} द्वारा आयोजित किया गया।", "{v} ने अनुबंध को मंजूरी दी।",
    "मूल्यांकन {v} द्वारा प्रस्तुत किया गया।", "{v} ने सत्र की अध्यक्षता की।",
]
_CTX_LOC_AR = [
    "سيُعقد المؤتمر في {v}.", "مكتبنا في {v} ينمو.",
    "تم إرسال الشحنة من {v}.", "العمليات في {v} تتوسع.",
    "تم افتتاح فرع جديد في {v}.",
]
_CTX_LOC_ZH = [
    "会议将在{v}举行。", "我们在{v}的办公室正在增长。",
    "货物已从{v}发出。", "{v}的业务正在扩展。",
    "新分公司在{v}开设。",
]
_CTX_LOC_JA = [
    "会議は{v}で開催されます。", "{v}の事務所は成長しています。",
    "荷物は{v}から発送されました。", "{v}での事業は拡大しています。",
    "新しい支店が{v}に開設されました。",
]
_CTX_LOC_HI = [
    "सम्मेलन {v} में होगा।", "{v} में हमारा कार्यालय बढ़ रहा है।",
    "शिपमेंट {v} से भेजा गया।", "{v} में संचालन का विस्तार हो रहा है।",
    "{v} में नई शाखा खोली गई।",
]

# --- Build multilingual NER documents ---

MULTILINGUAL_NER_DOCUMENTS: list[BenchmarkDocument] = (
    # Latin-script European languages
    _build_docs(PERSON_NAMES_DE, "PERSON_NAME", "ner_name_de", _CTX_NAME_DE, "de", 5)
    + _build_docs(PERSON_NAMES_FR, "PERSON_NAME", "ner_name_fr", _CTX_NAME_FR, "fr", 5)
    + _build_docs(PERSON_NAMES_ES, "PERSON_NAME", "ner_name_es", _CTX_NAME_ES, "es", 5)
    + _build_docs(PERSON_NAMES_PT, "PERSON_NAME", "ner_name_pt", _CTX_NAME_PT, "pt", 5)
    + _build_docs(PERSON_NAMES_IT, "PERSON_NAME", "ner_name_it", _CTX_NAME_IT, "it", 5)
    + _build_docs(PERSON_NAMES_NL, "PERSON_NAME", "ner_name_nl", _CTX_NAME_NL, "nl", 5)
    + _build_docs(PERSON_NAMES_PL, "PERSON_NAME", "ner_name_pl", _CTX_NAME_PL, "pl", 5)
    + _build_docs(PERSON_NAMES_RU, "PERSON_NAME", "ner_name_ru", _CTX_NAME_RU, "ru", 5)
    + _build_docs(LOCATIONS_DE, "LOCATION", "ner_loc_de", _CTX_LOC_DE, "de", 5)
    + _build_docs(LOCATIONS_FR, "LOCATION", "ner_loc_fr", _CTX_LOC_FR, "fr", 5)
    + _build_docs(LOCATIONS_ES, "LOCATION", "ner_loc_es", _CTX_LOC_ES, "es", 5)
    + _build_docs(LOCATIONS_PT, "LOCATION", "ner_loc_pt", _CTX_LOC_PT, "pt", 5)
    + _build_docs(LOCATIONS_IT, "LOCATION", "ner_loc_it", _CTX_LOC_IT, "it", 5)
    + _build_docs(LOCATIONS_NL, "LOCATION", "ner_loc_nl", _CTX_LOC_NL, "nl", 5)
    + _build_docs(LOCATIONS_PL, "LOCATION", "ner_loc_pl", _CTX_LOC_PL, "pl", 5)
    + _build_docs(LOCATIONS_RU, "LOCATION", "ner_loc_ru", _CTX_LOC_RU, "ru", 5)
    # Non-Latin scripts
    + _build_docs(PERSON_NAMES_AR, "PERSON_NAME", "ner_name_ar", _CTX_NAME_AR, "ar", 5)
    + _build_docs(PERSON_NAMES_ZH, "PERSON_NAME", "ner_name_zh", _CTX_NAME_ZH, "zh", 5)
    + _build_docs(PERSON_NAMES_JA, "PERSON_NAME", "ner_name_ja", _CTX_NAME_JA, "ja", 5)
    + _build_docs(PERSON_NAMES_HI, "PERSON_NAME", "ner_name_hi", _CTX_NAME_HI, "hi", 5)
    + _build_docs(LOCATIONS_AR, "LOCATION", "ner_loc_ar", _CTX_LOC_AR, "ar", 5)
    + _build_docs(LOCATIONS_ZH, "LOCATION", "ner_loc_zh", _CTX_LOC_ZH, "zh", 5)
    + _build_docs(LOCATIONS_JA, "LOCATION", "ner_loc_ja", _CTX_LOC_JA, "ja", 5)
    + _build_docs(LOCATIONS_HI, "LOCATION", "ner_loc_hi", _CTX_LOC_HI, "hi", 5)
    # ALL CAPS multilingual
    + _build_docs(PERSON_NAMES_CAPS_DE, "PERSON_NAME", "ner_caps_de", _CTX_NAME_CAPS_EN, "de", 5)
    + _build_docs(PERSON_NAMES_CAPS_FR, "PERSON_NAME", "ner_caps_fr", _CTX_NAME_CAPS_EN, "fr", 5)
    + _build_docs(PERSON_NAMES_CAPS_ES, "PERSON_NAME", "ner_caps_es", _CTX_NAME_CAPS_EN, "es", 5)
    + _build_docs(PERSON_NAMES_CAPS_RU, "PERSON_NAME", "ner_caps_ru", _CTX_NAME_CAPS_EN, "ru", 5)
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
    extra_types = (
        "IBAN", "ORGANIZATION_NAME",
        "DATE_OF_BIRTH", "MAC_ADDRESS", "URL", "COORDINATES",
        "COOKIE_ID", "DEVICE_ID", "SOCIAL_SECURITY_NUMBER", "CPF",
        "PASSPORT_NUMBER", "DRIVERS_LICENSE", "TAX_ID", "LICENSE_PLATE",
    )
    for etype in extra_types:
        if etype not in policy.entity_types:
            policy.entity_types.append(etype)
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
        from septum_api.services.ollama_client import call_ollama_sync
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
        try:
            _merge(r.per_type, _evaluate(sanitizer, doc))
            r.total_documents += 1
        except Exception as exc:
            logger.warning("Skipping doc %s (%s): %s", doc.name, doc.language, exc)
    return r


# ═══════════════════════════════════════════════════════════════════════════
#  TESTS
# ═══════════════════════════════════════════════════════════════════════════

def test_benchmark_presidio_layer(presidio_sanitizer: PIISanitizer) -> None:
    """Presidio layer: controlled-format entities, 8 core types, 17 regulations active."""
    r = _run_benchmark(presidio_sanitizer, PRESIDIO_DOCUMENTS,
                       "Presidio (Layer 1 — controlled format, 17 regulations)")
    print(f"\n\n=== Presidio Layer Benchmark (Controlled Format) ===\n{_report(r)}\n")
    assert _totals(r.per_type).recall >= 0.70


def test_benchmark_presidio_extended(presidio_sanitizer: PIISanitizer) -> None:
    """Presidio extended: 12 additional pattern-based entity types, 30 values each."""
    r = _run_benchmark(presidio_sanitizer, EXTENDED_PRESIDIO_DOCUMENTS,
                       "Presidio (Layer 1 — extended entity types)")
    print(f"\n\n=== Presidio Extended Benchmark ===\n{_report(r)}\n")
    # No minimum assertion — new recognizers, report detection baseline


def test_benchmark_presidio_adversarial(presidio_sanitizer: PIISanitizer) -> None:
    """Presidio adversarial: real-world edge cases (spaced IBANs, dotted phones, etc.)."""
    r = _run_benchmark(presidio_sanitizer, ADVERSARIAL_DOCUMENTS,
                       "Presidio (Layer 1 — adversarial / real-world formats)")
    print(f"\n\n=== Presidio Adversarial Benchmark ===\n{_report(r)}\n")
    # No minimum assertion — this tier honestly reports detection gaps


def test_presidio_no_zero_recall(presidio_sanitizer: PIISanitizer) -> None:
    """Every Presidio entity type must detect at least one value (controlled format)."""
    r = _run_benchmark(presidio_sanitizer, PRESIDIO_DOCUMENTS, "")
    for etype, m in r.per_type.items():
        assert m.recall > 0, f"{etype}: 0% recall ({m.fn} planted)"


def test_benchmark_ner_layer(ner_sanitizer: Optional[PIISanitizer]) -> None:
    """NER layer: 14 languages, PERSON_NAME (mixed + ALL CAPS) + LOCATION + ORGANIZATION_NAME."""
    if ner_sanitizer is None:
        pytest.skip("NER models not available")
    all_ner = NER_DOCUMENTS + MULTILINGUAL_NER_DOCUMENTS
    r = _run_benchmark(ner_sanitizer, all_ner,
                       "NER (Layer 2 — 14 languages, 17 regulations)")
    print(f"\n\n=== NER Layer Benchmark (14 languages) ===\n{_report(r)}\n")
    assert _totals(r.per_type).recall >= 0.40


def test_benchmark_ollama_layer(ollama_sanitizer: Optional[PIISanitizer]) -> None:
    """Ollama layer: alias/nickname detection + validation (model: aya-expanse:8b)."""
    if ollama_sanitizer is None:
        pytest.skip("Ollama not available")
    all_ner = NER_DOCUMENTS + MULTILINGUAL_NER_DOCUMENTS
    docs = all_ner + OLLAMA_ALIAS_DOCUMENTS
    r = _run_benchmark(ollama_sanitizer, docs,
                       f"Full Pipeline (L1+L2+L3 — Ollama {OLLAMA_MODEL}, 17 regulations)")
    print(f"\n\n=== Full Pipeline Benchmark (with Ollama {OLLAMA_MODEL}) ===\n{_report(r)}\n")
    assert _totals(r.per_type).recall >= 0.40


def test_benchmark_combined_summary(
    presidio_sanitizer: PIISanitizer,
    ner_sanitizer: Optional[PIISanitizer],
    ollama_sanitizer: Optional[PIISanitizer],
) -> None:
    """Print combined summary across all layers and auto-update charts + READMEs."""
    # Presidio: controlled + extended + adversarial
    p_r = _run_benchmark(presidio_sanitizer, PRESIDIO_DOCUMENTS, "Presidio")
    p_t = _totals(p_r.per_type)
    p_n = _planted(PRESIDIO_DOCUMENTS)

    e_r = _run_benchmark(presidio_sanitizer, EXTENDED_PRESIDIO_DOCUMENTS, "Extended")
    e_t = _totals(e_r.per_type)
    e_n = _planted(EXTENDED_PRESIDIO_DOCUMENTS)

    a_r = _run_benchmark(presidio_sanitizer, ADVERSARIAL_DOCUMENTS, "Adversarial")
    a_t = _totals(a_r.per_type)
    a_n = _planted(ADVERSARIAL_DOCUMENTS)

    print("\n\n" + "=" * 68)
    print("  SEPTUM PII DETECTION BENCHMARK — COMBINED SUMMARY")
    print("  All 17 built-in regulations active")
    print("=" * 68)
    print(f"\n  Layer 1a — Presidio (controlled format)")
    print(f"    Documents:  {p_r.total_documents}  |  Entities: {p_n}  |  Types: {len(p_r.per_type)}")
    print(f"    Precision: {p_t.precision:.1%}  |  Recall: {p_t.recall:.1%}  |  F1: {p_t.f1:.1%}")
    print(f"\n  Layer 1b — Presidio (extended entity types)")
    print(f"    Documents:  {e_r.total_documents}  |  Entities: {e_n}  |  Types: {len(e_r.per_type)}")
    print(f"    Precision: {e_t.precision:.1%}  |  Recall: {e_t.recall:.1%}  |  F1: {e_t.f1:.1%}")
    print(f"\n  Layer 1c — Presidio (adversarial / real-world)")
    print(f"    Documents:  {a_r.total_documents}  |  Entities: {a_n}  |  Types: {len(a_r.per_type)}")
    print(f"    Precision: {a_t.precision:.1%}  |  Recall: {a_t.recall:.1%}  |  F1: {a_t.f1:.1%}")

    # NER: EN/TR + multilingual
    all_ner_docs = NER_DOCUMENTS + MULTILINGUAL_NER_DOCUMENTS
    if ner_sanitizer is not None:
        n_r = _run_benchmark(ner_sanitizer, all_ner_docs, "NER")
        n_t = _totals(n_r.per_type)
        n_n = _planted(all_ner_docs)
        print(f"\n  Layer 2 — NER (14 languages)")
        print(f"    Documents:  {n_r.total_documents}  |  Entities: {n_n}  |  Types: {len(n_r.per_type)}")
        print(f"    Precision: {n_t.precision:.1%}  |  Recall: {n_t.recall:.1%}  |  F1: {n_t.f1:.1%}")
    else:
        n_r = None; n_t = None; n_n = 0
        print("\n  Layer 2 — NER: skipped (models not available)")

    # Ollama: full pipeline
    if ollama_sanitizer is not None:
        o_docs = all_ner_docs + OLLAMA_ALIAS_DOCUMENTS
        o_r = _run_benchmark(ollama_sanitizer, o_docs, "Ollama")
        o_t = _totals(o_r.per_type)
        o_n = _planted(o_docs)
        print(f"\n  Layer 3/4 — Ollama ({OLLAMA_MODEL})")
        print(f"    Documents:  {o_r.total_documents}  |  Entities: {o_n}  |  Types: {len(o_r.per_type)}")
        print(f"    Precision: {o_t.precision:.1%}  |  Recall: {o_t.recall:.1%}  |  F1: {o_t.f1:.1%}")
    else:
        o_r = None; o_t = None; o_n = 0
        print(f"\n  Layer 3/4 — Ollama: skipped (server not available)")

    # Grand total (Presidio controlled + extended + adversarial + NER + Ollama)
    all_tp = p_t.tp + e_t.tp + a_t.tp + (n_t.tp if n_t else 0) + (o_t.tp if o_t else 0)
    all_fp = p_t.fp + e_t.fp + a_t.fp + (n_t.fp if n_t else 0) + (o_t.fp if o_t else 0)
    all_fn = p_t.fn + e_t.fn + a_t.fn + (n_t.fn if n_t else 0) + (o_t.fn if o_t else 0)
    all_n = p_n + e_n + a_n + n_n + o_n
    pr = all_tp / (all_tp + all_fp) if (all_tp + all_fp) else 0
    rc = all_tp / (all_tp + all_fn) if (all_tp + all_fn) else 0
    f1 = 2 * pr * rc / (pr + rc) if (pr + rc) else 0
    print(f"\n  Grand Total")
    print(f"    Entities: {all_n}  |  Precision: {pr:.1%}  |  Recall: {rc:.1%}  |  F1: {f1:.1%}")
    print("\n" + "=" * 68 + "\n")

    # --- Auto-update charts and README tables ---
    # Merge Presidio controlled + extended + adversarial into one Presidio report for charts
    merged_presidio_per_type: Dict[str, EntityMetrics] = {}
    _merge(merged_presidio_per_type, p_r.per_type)
    _merge(merged_presidio_per_type, e_r.per_type)
    _merge(merged_presidio_per_type, a_r.per_type)
    merged_p_t = _totals(merged_presidio_per_type)
    merged_p_n = p_n + e_n + a_n

    layer_data = {
        "presidio": {"entities": merged_p_n, "types": len(merged_presidio_per_type),
                     "precision": merged_p_t.precision, "recall": merged_p_t.recall, "f1": merged_p_t.f1,
                     "per_type": merged_presidio_per_type},
        "ner": {"entities": n_n, "types": len(n_r.per_type) if n_t else 0,
                "precision": n_t.precision if n_t else 0, "recall": n_t.recall if n_t else 0,
                "f1": n_t.f1 if n_t else 0,
                "per_type": n_r.per_type if n_r else {}},
        "ollama": {"entities": o_n, "types": len(o_r.per_type) if o_t else 0,
                   "precision": o_t.precision if o_t else 0, "recall": o_t.recall if o_t else 0,
                   "f1": o_t.f1 if o_t else 0,
                   "per_type": o_r.per_type if o_r else {}},
        "combined": {"entities": all_n, "types": len(set(
            list(merged_presidio_per_type) + (list(n_r.per_type) if n_r else []) + (list(o_r.per_type) if o_r else [])
        )), "precision": pr, "recall": rc, "f1": f1},
    }
    _generate_charts(layer_data)
    _update_readmes(layer_data)


# ═══════════════════════════════════════════════════════════════════════════
#  AUTO-UPDATE: Charts + README tables
# ═══════════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _generate_charts(data: dict) -> None:
    """Regenerate benchmark PNG charts from live results."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available — skipping chart generation")
        return

    out_dir = _PROJECT_ROOT / "screenshots"
    out_dir.mkdir(exist_ok=True)

    # --- Chart 1: F1 by entity type ---
    presidio_types = sorted(data["presidio"]["per_type"].keys())
    ner_types = sorted(data["ner"]["per_type"].keys())

    labels, scores, colors = [], [], []
    short = {"EMAIL_ADDRESS": "Email", "PHONE_NUMBER": "Phone", "IP_ADDRESS": "IP",
             "CREDIT_CARD_NUMBER": "Credit Card", "IBAN": "IBAN", "NATIONAL_ID": "National ID",
             "MEDICAL_RECORD_NUMBER": "MRN", "HEALTH_INSURANCE_ID": "Health Ins.",
             "PERSON_NAME": "Person Name", "LOCATION": "Location",
             "ORGANIZATION_NAME": "Organization",
             "DATE_OF_BIRTH": "DOB", "MAC_ADDRESS": "MAC", "URL": "URL",
             "COORDINATES": "Coords", "COOKIE_ID": "Cookie ID",
             "DEVICE_ID": "Device ID", "SOCIAL_SECURITY_NUMBER": "SSN",
             "CPF": "CPF", "PASSPORT_NUMBER": "Passport",
             "DRIVERS_LICENSE": "DL", "TAX_ID": "Tax ID",
             "LICENSE_PLATE": "Plate"}
    for et in presidio_types:
        m = data["presidio"]["per_type"][et]
        labels.append(short.get(et, et))
        scores.append(m.f1 * 100)
        colors.append("#6C8EBF")
    for et in ner_types:
        m = data["ner"]["per_type"][et]
        labels.append(short.get(et, et))
        scores.append(m.f1 * 100)
        colors.append("#9B7ED8")

    fig, ax = plt.subplots(figsize=(max(16, len(labels) * 0.8), 5.5))
    bars = ax.bar(labels, scores, color=colors, width=0.65, edgecolor="white", linewidth=0.5)
    for bar, val in zip(bars, scores):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 1,
                f"{val:.1f}%" if val < 100 else "100%",
                ha="center", va="bottom", fontsize=8, fontweight="bold", color="#333")
    ax.set_ylim(0, 115)
    ax.set_ylabel("F1 Score (%)", fontsize=11)
    ax.set_title("PII Detection F1 Score by Entity Type", fontsize=13, fontweight="bold", pad=12)
    ax.tick_params(axis="x", labelsize=8)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    ax.tick_params(axis="y", labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor="#6C8EBF", label="Presidio (Layer 1)"),
                       Patch(facecolor="#9B7ED8", label="NER (Layer 2)")],
              loc="lower right", fontsize=9)
    plt.tight_layout()
    plt.savefig(str(out_dir / "benchmark-f1-by-type.png"), dpi=180, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close()

    # --- Chart 2: Accuracy by layer ---
    layers_labels = ["Presidio\n(Layer 1)", "NER\n(Layer 2)", "Ollama\n(Layer 3)", "Combined\n(All Layers)"]
    precision = [data[k]["precision"] * 100 for k in ("presidio", "ner", "ollama", "combined")]
    recall = [data[k]["recall"] * 100 for k in ("presidio", "ner", "ollama", "combined")]
    f1 = [data[k]["f1"] * 100 for k in ("presidio", "ner", "ollama", "combined")]

    x = np.arange(len(layers_labels))
    w = 0.22
    fig, ax = plt.subplots(figsize=(10, 5.5))
    b1 = ax.bar(x - w, precision, w, label="Precision", color="#4CAF88", edgecolor="white", linewidth=0.5)
    b2 = ax.bar(x, recall, w, label="Recall", color="#5B9BD5", edgecolor="white", linewidth=0.5)
    b3 = ax.bar(x + w, f1, w, label="F1", color="#F0A030", edgecolor="white", linewidth=0.5)
    for bars_group in [b1, b2, b3]:
        for bar in bars_group:
            val = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, val + 0.2,
                    f"{val:.1f}", ha="center", va="bottom", fontsize=9, fontweight="bold", color="#333")
    ax.set_ylim(90, 102)
    ax.set_ylabel("Score (%)", fontsize=11)
    ax.set_title("Detection Accuracy by Pipeline Layer", fontsize=13, fontweight="bold", pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels(layers_labels, fontsize=10)
    ax.tick_params(axis="y", labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    ax.legend(loc="lower right", fontsize=10)
    plt.tight_layout()
    plt.savefig(str(out_dir / "benchmark-layer-comparison.png"), dpi=180, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close()
    logger.info("Benchmark charts regenerated in %s", out_dir)


def _fmt(v: float) -> str:
    """Format a percentage value for README tables."""
    r = f"{v * 100:.1f}%"
    return r.replace(".0%", "%")


def _fmt_tr(v: float) -> str:
    """Format a percentage for the Turkish README (comma decimal)."""
    r = f"{v * 100:.1f}"
    if r.endswith(".0"):
        return f"%{r[:-2]}"
    return f"%{r.replace('.', ',')}"


def _update_readmes(data: dict) -> None:
    """Update benchmark tables in README.md and README.tr.md from live results."""
    p, n, o, c = data["presidio"], data["ner"], data["ollama"], data["combined"]

    # --- English README ---
    en_table = (
        "| Layer | Entities | Types | Precision | Recall | F1 |\n"
        "|---|:---:|:---:|:---:|:---:|:---:|\n"
        f"| Presidio (L1) — patterns + validators | {p['entities']:,} | {p['types']} | {_fmt(p['precision'])} | {_fmt(p['recall'])} | {_fmt(p['f1'])} |\n"
        f"| NER (L2) — XLM-RoBERTa + ALL CAPS normalisation | {n['entities']} | {n['types']} | {_fmt(n['precision'])} | {_fmt(n['recall'])} | {_fmt(n['f1'])} |\n"
        f"| Ollama (L3) — aya-expanse:8b | {o['entities']} | {o['types']} | {_fmt(o['precision'])} | {_fmt(o['recall'])} | {_fmt(o['f1'])} |\n"
        f"| **Combined** | **{c['entities']:,}** | **{c['types']}** | **{_fmt(c['precision'])}** | **{_fmt(c['recall'])}** | **{_fmt(c['f1'])}** |"
    )

    en_desc = (
        f"All 17 built-in regulations active. **{c['entities']:,} algorithmically generated PII values** "
        f"across {c['types']} entity types"
    )

    readme_en = _PROJECT_ROOT / "README.md"
    _replace_block(readme_en, "| Layer | Entities | Types |", "| **Combined**", en_table)
    _replace_description(readme_en, "All 17 built-in regulations active.", en_desc)

    # --- Turkish README ---
    tr_table = (
        "| Katman | Varlıklar | Tipler | Precision | Recall | F1 |\n"
        "|---|:---:|:---:|:---:|:---:|:---:|\n"
        f"| Presidio (K1) — desenler + doğrulayıcılar | {p['entities']:,} | {p['types']} | {_fmt_tr(p['precision'])} | {_fmt_tr(p['recall'])} | {_fmt_tr(p['f1'])} |\n"
        f"| NER (K2) — XLM-RoBERTa + BÜYÜK HARF normalizasyonu | {n['entities']} | {n['types']} | {_fmt_tr(n['precision'])} | {_fmt_tr(n['recall'])} | {_fmt_tr(n['f1'])} |\n"
        f"| Ollama (K3) — aya-expanse:8b | {o['entities']} | {o['types']} | {_fmt_tr(o['precision'])} | {_fmt_tr(o['recall'])} | {_fmt_tr(o['f1'])} |\n"
        f"| **Birleşik** | **{c['entities']:,}** | **{c['types']}** | **{_fmt_tr(c['precision'])}** | **{_fmt_tr(c['recall'])}** | **{_fmt_tr(c['f1'])}** |"
    )

    tr_desc = (
        f"Tüm 17 yerleşik regülasyon aktif. {c['types']} varlık tipinde **{c['entities']:,} algoritmik olarak üretilmiş PII değeri**"
    )

    readme_tr = _PROJECT_ROOT / "README.tr.md"
    _replace_block(readme_tr, "| Katman | Varlıklar | Tipler |", "| **Birleşik**", tr_table)
    _replace_description(readme_tr, "Tüm 17 yerleşik regülasyon aktif.", tr_desc)

    logger.info("README benchmark tables updated")


def _replace_block(path: Path, header_start: str, last_row_start: str, new_block: str) -> None:
    """Replace a markdown table block identified by header and last row."""
    text = path.read_text("utf-8")
    lines = text.split("\n")
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if header_start in line and start_idx is None:
            start_idx = i
        if start_idx is not None and last_row_start in line:
            end_idx = i
            break
    if start_idx is not None and end_idx is not None:
        lines[start_idx:end_idx + 1] = new_block.split("\n")
        path.write_text("\n".join(lines), "utf-8")


def _replace_description(path: Path, anchor: str, new_line: str) -> None:
    """Replace a line starting with *anchor* up to the next period-terminated phrase."""
    text = path.read_text("utf-8")
    pattern = re.escape(anchor) + r".*?(?=\s*\(valid|\s*\(geçerli)"
    match = re.search(pattern, text)
    if match:
        text = text[:match.start()] + new_line + text[match.end():]
        path.write_text(text, "utf-8")
