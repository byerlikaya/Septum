from __future__ import annotations

"""Built-in regulation ruleset seed data for Septum.

Regulation metadata (display name, region, description, URL) lives here.
Entity type lists are imported from ``septum_core`` to keep a single
source of truth — MCP and standalone engine callers load the same
``ENTITY_TYPES`` constant from each pack's ``__init__`` module.
"""

import importlib
import os
from typing import List

from ..models.regulation import RegulationRuleset


def _entity_types(reg_id: str) -> List[str]:
    pkg = importlib.import_module(f"septum_core.recognizers.{reg_id}")
    return list(getattr(pkg, "ENTITY_TYPES"))


_REGULATION_META = [
    (
        "gdpr",
        "General Data Protection Regulation",
        "EU / EEA",
        "Regulation (EU) 2016/679. Personal data: Art. 4(1). Special categories: Art. 9(1). Online identifiers: Rec. 30.",
        "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
    ),
    (
        "hipaa",
        "Health Insurance Portability and Accountability Act",
        "USA (Healthcare)",
        "US regulation governing protected health information (PHI).",
        "https://www.hhs.gov/hipaa/index.html",
    ),
    (
        "kvkk",
        "Personal Data Protection Law (Turkey)",
        "Turkey",
        "6698 sayılı KVKK. Madde 3(d): kişisel veri tanımı. Madde 6: özel nitelikli kişisel veriler (ırk, etnik köken, siyasi düşünce, din, sağlık, cinsel hayat, biyometrik, genetik vb.). Kurum rehberi: ad, soyad, ana/baba adı, adres, doğum tarihi, telefon, plaka, SGK, pasaport.",
        "https://www.kvkk.gov.tr/",
    ),
    (
        "lgpd",
        "Lei Geral de Proteção de Dados",
        "Brazil",
        "Brazilian General Data Protection Law (LGPD).",
        "https://www.gov.br/escola-national-de-administracao-publica/lgpd",
    ),
    (
        "ccpa",
        "California Consumer Privacy Act",
        "USA (California)",
        "Cal. Civ. Code § 1798.140. Identifiers, customer records, protected classifications, biometric, geolocation, sensitive PI.",
        "https://oag.ca.gov/privacy/ccpa",
    ),
    (
        "cpra",
        "California Privacy Rights Act",
        "USA (California)",
        "CPRA amends CCPA; Cal. Civ. Code § 1798.140. Same categories plus sensitive personal information (precise geolocation, genetic, health).",
        "https://oag.ca.gov/privacy/cpra",
    ),
    (
        "uk_gdpr",
        "UK GDPR",
        "United Kingdom",
        "UK GDPR (retained EU law) and DPA 2018. Same personal data definition as EU GDPR Art. 4(1); special categories Art. 9(1); ICO guidance on identifiers.",
        "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/",
    ),
    (
        "pipeda",
        "Personal Information Protection and Electronic Documents Act",
        "Canada",
        "PIPEDA s. 2(1): information about an identifiable individual. OPC guidance: financial, biometric, health, identifiers, opinions.",
        "https://laws-lois.justice.gc.ca/eng/acts/P-8.6/",
    ),
    (
        "pdpa_th",
        "Personal Data Protection Act",
        "Thailand",
        "Thailand PDPA (B.E. 2562). Section 6 personal data; Section 26 sensitive data.",
        "https://www.pdpathailand.com/",
    ),
    (
        "pdpa_sg",
        "Personal Data Protection Act",
        "Singapore",
        "Singapore PDPA.",
        "https://www.pdpc.gov.sg/",
    ),
    (
        "appi",
        "Act on the Protection of Personal Information",
        "Japan",
        "Japan APPI. Art. 2(1) personal information; Art. 2(3) special care-required.",
        "https://www.ppc.go.jp/en/",
    ),
    (
        "pipl",
        "Personal Information Protection Law",
        "China",
        "China PIPL. Art. 4 personal information; Art. 28 sensitive personal information.",
        "https://www.cac.gov.cn/",
    ),
    (
        "popia",
        "Protection of Personal Information Act",
        "South Africa",
        "South Africa POPIA (Act 4 of 2013). Section 1 definitions; Section 26 special personal information.",
        "https://popia.co.za/",
    ),
    (
        "dpdp",
        "Digital Personal Data Protection Act",
        "India",
        "India DPDP Act 2023.",
        "https://www.meity.gov.in/data-protection-framework",
    ),
    (
        "pdpl_sa",
        "Personal Data Protection Law",
        "Saudi Arabia",
        "Saudi Arabia PDPL (Royal Decree M/19). Art. 1 definitions; Art. 14 sensitive data.",
        "https://sdaia.gov.sa/",
    ),
    (
        "nzpa",
        "Privacy Act 2020",
        "New Zealand",
        "New Zealand Privacy Act 2020. Section 7 definition; OPC sensitive data guidance.",
        "https://www.privacy.org.nz/",
    ),
    (
        "australia_pa",
        "Privacy Act 1988",
        "Australia",
        "Australia Privacy Act 1988 (amended). Section 6(1) personal/sensitive information.",
        "https://www.oaic.gov.au/privacy/",
    ),
]


def builtin_regulations() -> list[RegulationRuleset]:
    """Return the built-in regulation rulesets to seed into the database."""
    all_builtin_ids = ",".join(reg_id for reg_id, *_ in _REGULATION_META)
    default_active_regs_env = os.getenv(
        "DEFAULT_ACTIVE_REGULATIONS", all_builtin_ids
    ).strip()
    default_active_regulations: List[str] = [
        r.strip().lower() for r in default_active_regs_env.split(",") if r.strip()
    ] or all_builtin_ids.split(",")

    def is_active(reg_id: str) -> bool:
        return reg_id.lower() in default_active_regulations

    return [
        RegulationRuleset(
            id=reg_id,
            display_name=display_name,
            region=region,
            description=description,
            official_url=url,
            entity_types=_entity_types(reg_id),
            is_builtin=True,
            is_active=is_active(reg_id),
            custom_notes=None,
        )
        for reg_id, display_name, region, description, url in _REGULATION_META
    ]
