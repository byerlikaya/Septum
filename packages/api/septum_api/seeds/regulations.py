from __future__ import annotations

"""Built-in regulation ruleset seed data for Septum.

Regulation metadata (display name, region, description, URL) lives here.
Entity type lists are imported from ``septum_core`` to keep a single
source of truth — MCP and standalone engine callers load the same
``ENTITY_TYPES`` constant from each pack's ``__init__`` module.
"""

import os
from typing import List, NamedTuple

from septum_core.recognizers import BUILTIN_REGULATION_IDS, entity_types_for

from ..models.regulation import RegulationRuleset


class _RegulationMeta(NamedTuple):
    id: str
    display_name: str
    region: str
    description: str
    official_url: str


def _entity_types(reg_id: str) -> List[str]:
    types = entity_types_for(reg_id)
    if not types:
        raise RuntimeError(
            f"Regulation pack '{reg_id}' is missing ENTITY_TYPES; "
            f"declare it in septum_core/recognizers/{reg_id}/__init__.py"
        )
    return types


_REGULATION_META: list[_RegulationMeta] = [
    _RegulationMeta(
        "gdpr",
        "General Data Protection Regulation",
        "EU / EEA",
        "Regulation (EU) 2016/679. Personal data: Art. 4(1). Special categories: Art. 9(1). Online identifiers: Rec. 30.",
        "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
    ),
    _RegulationMeta(
        "hipaa",
        "Health Insurance Portability and Accountability Act",
        "USA (Healthcare)",
        "US regulation governing protected health information (PHI).",
        "https://www.hhs.gov/hipaa/index.html",
    ),
    _RegulationMeta(
        "kvkk",
        "Personal Data Protection Law (Turkey)",
        "Turkey",
        "6698 sayılı KVKK. Madde 3(d): kişisel veri tanımı. Madde 6: özel nitelikli kişisel veriler (ırk, etnik köken, siyasi düşünce, din, sağlık, cinsel hayat, biyometrik, genetik vb.). Kurum rehberi: ad, soyad, ana/baba adı, adres, doğum tarihi, telefon, plaka, SGK, pasaport.",
        "https://www.kvkk.gov.tr/",
    ),
    _RegulationMeta(
        "lgpd",
        "Lei Geral de Proteção de Dados",
        "Brazil",
        "Brazilian General Data Protection Law (LGPD).",
        "https://www.gov.br/escola-national-de-administracao-publica/lgpd",
    ),
    _RegulationMeta(
        "ccpa",
        "California Consumer Privacy Act",
        "USA (California)",
        "Cal. Civ. Code § 1798.140. Identifiers, customer records, protected classifications, biometric, geolocation, sensitive PI.",
        "https://oag.ca.gov/privacy/ccpa",
    ),
    _RegulationMeta(
        "cpra",
        "California Privacy Rights Act",
        "USA (California)",
        "CPRA amends CCPA; Cal. Civ. Code § 1798.140. Same categories plus sensitive personal information (precise geolocation, genetic, health).",
        "https://oag.ca.gov/privacy/cpra",
    ),
    _RegulationMeta(
        "uk_gdpr",
        "UK GDPR",
        "United Kingdom",
        "UK GDPR (retained EU law) and DPA 2018. Same personal data definition as EU GDPR Art. 4(1); special categories Art. 9(1); ICO guidance on identifiers.",
        "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/",
    ),
    _RegulationMeta(
        "pipeda",
        "Personal Information Protection and Electronic Documents Act",
        "Canada",
        "PIPEDA s. 2(1): information about an identifiable individual. OPC guidance: financial, biometric, health, identifiers, opinions.",
        "https://laws-lois.justice.gc.ca/eng/acts/P-8.6/",
    ),
    _RegulationMeta(
        "pdpa_th",
        "Personal Data Protection Act",
        "Thailand",
        "Thailand PDPA (B.E. 2562). Section 6 personal data; Section 26 sensitive data.",
        "https://www.pdpathailand.com/",
    ),
    _RegulationMeta(
        "pdpa_sg",
        "Personal Data Protection Act",
        "Singapore",
        "Singapore PDPA.",
        "https://www.pdpc.gov.sg/",
    ),
    _RegulationMeta(
        "appi",
        "Act on the Protection of Personal Information",
        "Japan",
        "Japan APPI. Art. 2(1) personal information; Art. 2(3) special care-required.",
        "https://www.ppc.go.jp/en/",
    ),
    _RegulationMeta(
        "pipl",
        "Personal Information Protection Law",
        "China",
        "China PIPL. Art. 4 personal information; Art. 28 sensitive personal information.",
        "https://www.cac.gov.cn/",
    ),
    _RegulationMeta(
        "popia",
        "Protection of Personal Information Act",
        "South Africa",
        "South Africa POPIA (Act 4 of 2013). Section 1 definitions; Section 26 special personal information.",
        "https://popia.co.za/",
    ),
    _RegulationMeta(
        "dpdp",
        "Digital Personal Data Protection Act",
        "India",
        "India DPDP Act 2023.",
        "https://www.meity.gov.in/data-protection-framework",
    ),
    _RegulationMeta(
        "pdpl_sa",
        "Personal Data Protection Law",
        "Saudi Arabia",
        "Saudi Arabia PDPL (Royal Decree M/19). Art. 1 definitions; Art. 14 sensitive data.",
        "https://sdaia.gov.sa/",
    ),
    _RegulationMeta(
        "nzpa",
        "Privacy Act 2020",
        "New Zealand",
        "New Zealand Privacy Act 2020. Section 7 definition; OPC sensitive data guidance.",
        "https://www.privacy.org.nz/",
    ),
    _RegulationMeta(
        "australia_pa",
        "Privacy Act 1988",
        "Australia",
        "Australia Privacy Act 1988 (amended). Section 6(1) personal/sensitive information.",
        "https://www.oaic.gov.au/privacy/",
    ),
]


_META_IDS = frozenset(meta.id for meta in _REGULATION_META)
assert _META_IDS == set(BUILTIN_REGULATION_IDS), (
    "Regulation metadata drift: "
    f"meta={_META_IDS ^ set(BUILTIN_REGULATION_IDS)}"
)


def builtin_regulations() -> list[RegulationRuleset]:
    """Return the built-in regulation rulesets to seed into the database."""
    all_builtin_ids = ",".join(BUILTIN_REGULATION_IDS)
    default_active_regs_env = os.getenv(
        "DEFAULT_ACTIVE_REGULATIONS", all_builtin_ids
    ).strip()
    default_active_regulations: List[str] = [
        r.strip().lower() for r in default_active_regs_env.split(",") if r.strip()
    ] or list(BUILTIN_REGULATION_IDS)

    def is_active(reg_id: str) -> bool:
        return reg_id.lower() in default_active_regulations

    return [
        RegulationRuleset(
            id=meta.id,
            display_name=meta.display_name,
            region=meta.region,
            description=meta.description,
            official_url=meta.official_url,
            entity_types=_entity_types(meta.id),
            is_builtin=True,
            is_active=is_active(meta.id),
            custom_notes=None,
        )
        for meta in _REGULATION_META
    ]
