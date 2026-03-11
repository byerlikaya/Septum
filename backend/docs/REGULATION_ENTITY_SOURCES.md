# Regulation entity types – legal and official sources

This document describes how the **entity types** for each built-in regulation are determined and which legal or official texts they align with. Entity types drive which PII the sanitizer detects when a regulation is active.

---

## GDPR – General Data Protection Regulation (EU 2016/679)

- **Legal basis:** Regulation (EU) 2016/679 (EUR-Lex), **Article 4(1)** (definition of personal data), **Article 4(13)–(15)** (genetic, biometric, health data), **Article 9(1)** (special categories), **Recital 30** (online identifiers).
- **Definition (Art. 4(1) – exact):** “Personal data” means any information relating to an identified or identifiable natural person (“data subject”); an identifiable natural person is one who can be identified, directly or indirectly, in particular by reference to an identifier such as **a name, an identification number, location data, an online identifier** or to **one or more factors specific to the physical, physiological, genetic, mental, economic, cultural or social identity** of that natural person.
- **Special categories (Art. 9(1) – exact):** Processing of personal data revealing **racial or ethnic origin, political opinions, religious or philosophical beliefs, or trade union membership**, and the processing of **genetic data, biometric data** for the purpose of uniquely identifying a natural person, **data concerning health** or **data concerning a natural person’s sex life or sexual orientation** shall be prohibited (unless an exception in Art. 9(2) applies).
- **Recital 30:** Natural persons may be associated with online identifiers provided by their devices, applications, tools and protocols, such as **IP addresses, cookie identifiers** or **other identifiers** (e.g. RFID). This may leave traces which, combined with unique identifiers and other information received by the servers, may be used to create profiles and identify individuals.
- **Entity-type mapping:** Name → `PERSON_NAME`, `FIRST_NAME`, `LAST_NAME`; identification number → `NATIONAL_ID`, `PASSPORT_NUMBER`, `TAX_ID`; location → `POSTAL_ADDRESS`, `STREET_ADDRESS`, `CITY`, `LOCATION`, `COORDINATES`; online identifier → `IP_ADDRESS`, `MAC_ADDRESS`, `COOKIE_ID`, `DEVICE_ID`; physical/physiological/health → `BIOMETRIC_ID`, `HEALTH_INSURANCE_ID`, `DIAGNOSIS`, `MEDICATION`, `CLINICAL_NOTE`, `DATE_OF_BIRTH`; economic → `FINANCIAL_ACCOUNT`, `CREDIT_CARD_NUMBER`, `BANK_ACCOUNT_NUMBER`; cultural/social/special → `ETHNICITY`, `POLITICAL_OPINION`, `RELIGION`, `SEXUAL_ORIENTATION`.

**References:** [EUR-Lex 2016/679](https://eur-lex.europa.eu/eli/reg/2016/679/oj), [GDPR Article 4](https://gdpr.eu.org/art/4/), [Article 9](https://gdpr.eu.org/art/9/).

---

## HIPAA – Health Insurance Portability and Accountability Act (USA)

- **Legal basis:** HIPAA Privacy Rule, **45 CFR Part 164, Subpart E**; Safe Harbor de-identification requires removal of **18 identifiers** (HHS Guidance on De-identification of PHI).
- **18 PHI identifiers (summary):** (1) Names; (2) Geographic data smaller than state (street, city, county, zip, geocodes); (3) Dates (birth, admission, discharge, death, and ages over 89); (4) Telephone numbers; (5) Fax numbers; (6) Email; (7) SSN; (8) Medical record numbers; (9) Health plan beneficiary numbers; (10) Account numbers; (11) Certificate/license numbers; (12) Vehicle identifiers and license plates; (13) Device identifiers and serial numbers; (14) Web URLs; (15) IP addresses; (16) Biometric identifiers; (17) Full-face photos; (18) Any other unique identifying number, characteristic, or code.
- **Entity-type mapping:** Names → `PERSON_NAME`; dates → `DATE_OF_BIRTH`; contact → `PHONE_NUMBER`, `EMAIL_ADDRESS`; geographic → `POSTAL_ADDRESS`, `CITY`; MRN → `MEDICAL_RECORD_NUMBER`; health plan → `HEALTH_INSURANCE_ID`; clinical → `DIAGNOSIS`, `MEDICATION`, `CLINICAL_NOTE`; SSN → `SOCIAL_SECURITY_NUMBER`; vehicle → `LICENSE_PLATE`; device → `DEVICE_ID`; online → `IP_ADDRESS`, `URL`; biometric → `BIOMETRIC_ID`.

**References:** [HHS HIPAA](https://www.hhs.gov/hipaa/index.html), [18 PHI Identifiers (Berkeley)](https://cphs.berkeley.edu/hipaa/hipaa18.html), [HHS De-identification Guidance](https://www.hhs.gov/sites/default/files/ocr/privacy/hipaa/understanding/coveredentities/De-identification/hhs_deid_guidance.pdf).

---

## KVKK – Kişisel Verilerin Korunması Kanunu (6698, Turkey)

- **Legal basis:** 6698 sayılı Kişisel Verilerin Korunması Kanunu (KVKK); Resmî Gazete 7 Nisan 2016, Sayı: 29677. Kişisel Verileri Koruma Kurumu (KVKK) rehberleri ve “Örneklerle Kişisel Verilerin Korunması” dokümanı.
- **Definition (Madde 3(d) – exact):** “Kişisel veri: **Kimliği belirli veya belirlenebilir gerçek kişiye ilişkin her türlü bilgiyi** ifade eder.” Tanım kapsamındaki bilgiler, kişinin fiziksel, ekonomik, kültürel, sosyal veya psikolojik kimliğini ifade eden veya kimlik/vergi/sigorta numarası gibi bir kayıtla ilişkilendirilerek kişiyi belirleyen bilgileri kapsar.
- **Kurum örnekleri (kişisel veri):** Ad, soyad, doğum tarihi, doğum yeri; fiziki, ailevi, ekonomik ve diğer özelliklere ilişkin bilgiler; isim, telefon numarası, motorlu taşıt plakası; sosyal güvenlik numarası, pasaport numarası; rehberde ayrıca: ana adı, baba adı, adres, SGK.
- **Özel nitelikli kişisel veriler (Madde 6 – sınırlı sayıda, kıyasla genişletilemez):** (1) Irk ve etnik köken, (2) Siyasi düşünce, (3) Felsefi inanç, (4) Din, mezhep veya diğer inançlar, (5) Kılık ve kıyafet, (6) Dernek, vakıf ya da sendika üyeliği, (7) Sağlık verileri, (8) Cinsel hayat verileri, (9) Ceza mahkûmiyeti ve güvenlik tedbirleriyle ilgili veriler, (10) Biyometrik veriler, (11) Genetik veriler.
- **Entity-type mapping:** Kimlik/iletişim → `PERSON_NAME`, `FIRST_NAME`, `LAST_NAME`, `NATIONAL_ID`, `PASSPORT_NUMBER`, `SOCIAL_SECURITY_NUMBER`, `TAX_ID`, `PHONE_NUMBER`, `EMAIL_ADDRESS`; adres/konum → `POSTAL_ADDRESS`, `STREET_ADDRESS`, `CITY`, `LOCATION`, `COORDINATES`; tarih/araç → `DATE_OF_BIRTH`, `LICENSE_PLATE`; çevrimiçi tanımlayıcı → `IP_ADDRESS`, `COOKIE_ID`, `DEVICE_ID`; özel nitelikli → `BIOMETRIC_ID`, `DNA_PROFILE`, `HEALTH_INSURANCE_ID`, `DIAGNOSIS`, `MEDICATION`, `CLINICAL_NOTE`, `RELIGION`, `ETHNICITY`, `POLITICAL_OPINION`, `SEXUAL_ORIENTATION`; ekonomik → `FINANCIAL_ACCOUNT`, `CREDIT_CARD_NUMBER`, `BANK_ACCOUNT_NUMBER`.

**References:** [kvkk.gov.tr](https://www.kvkk.gov.tr/), [Kişisel Veri / Özel Nitelikli Kişisel Veri (Kurum)](https://www.kvkk.gov.tr/Icerik/6605/Personal-Data-Special-Categories-of-Personal-Data), [Örneklerle Kişisel Verilerin Korunması](https://www.kvkk.gov.tr/Icerik/5521/Orneklerle-Kisisel-Verilerin-Korunmasi-Dokumani-Kurum-Internet-Sayfasinda-Yayinlanmistir-), [Rehberler](https://www.kvkk.gov.tr/Icerik/2030/rehberler).

---

## LGPD – Lei Geral de Proteção de Dados (Brazil, Law 13.709/2018)

- **Legal basis:** Lei nº 13.709/2018 (LGPD); ANPD guidance.
- **Definition:** Personal data: any information relating to an identified or identifiable natural person. **Sensitive personal data** (Art. 5 II): racial or ethnic origin, religious belief, political opinion, union membership, genetic/biometric data, health, sex life.
- **Examples (dados pessoais):** name, date/place of birth, RG/CPF, photo, address, email, card numbers, income, payment history, location, IP, cookies, phone.
- **Entity-type mapping:** Names → `PERSON_NAME`; IDs → `CPF`, `NATIONAL_ID`; contact → `EMAIL_ADDRESS`, `PHONE_NUMBER`, `POSTAL_ADDRESS`; dates → `DATE_OF_BIRTH`; location → `LOCATION`; online → `IP_ADDRESS`, `COOKIE_ID`; financial → `CREDIT_CARD_NUMBER`, `BANK_ACCOUNT_NUMBER`; health → `HEALTH_INSURANCE_ID`, `DIAGNOSIS`; sensitive → `BIOMETRIC_ID`, `POLITICAL_OPINION`, `RELIGION`, `ETHNICITY`, `SEXUAL_ORIENTATION`.

**References:** [Gov.br LGPD](https://www.gov.br/escola-national-de-administracao-publica/lgpd), [ANPD](https://www.gov.br/anpd/pt-br).

---

## PIPEDA – Personal Information Protection and Electronic Documents Act (Canada)

- **Legal basis:** PIPEDA, **section 2(1)** (definition of personal information); Office of the Privacy Commissioner (OPC) interpretation bulletins.
- **Definition (s. 2(1) – exact):** “Personal information” means **information about an identifiable individual**.
- **OPC guidance:** Identifiability = serious possibility that an individual could be identified (alone or in combination). Examples: financial/credit history, opinions about the individual, photographs, fingerprints, voice prints, blood type, video/audio where the individual appears or is heard.
- **Entity-type mapping:** Identifiers → `PERSON_NAME`, `FIRST_NAME`, `LAST_NAME`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `POSTAL_ADDRESS`, `STREET_ADDRESS`, `CITY`, `DATE_OF_BIRTH`, `NATIONAL_ID`; financial → `FINANCIAL_ACCOUNT`, `CREDIT_CARD_NUMBER`, `BANK_ACCOUNT_NUMBER`, `TAX_ID`; health → `HEALTH_INSURANCE_ID`, `DIAGNOSIS`, `MEDICATION`; online → `IP_ADDRESS`, `COOKIE_ID`, `DEVICE_ID`, `LOCATION`; biometric/sensitive → `BIOMETRIC_ID`, `RELIGION`, `ETHNICITY`.

**References:** [PIPEDA s. 2](https://laws-lois.justice.gc.ca/eng/acts/P-8.6/section-2.html), [OPC PIPEDA](https://www.priv.gc.ca/en/privacy-topics/privacy-laws-in-canada/the-personal-information-protection-and-electronic-documents-act-pipeda/).

---

## CCPA / CPRA – California Consumer Privacy Act (USA, California)

- **Legal basis:** Cal. Civ. Code § **1798.140** (definitions); California Privacy Protection Agency (CPPA). CPRA amends CCPA and adds “sensitive personal information.”
- **Definition (§ 1798.140(o)(1)):** “Personal information” means information that identifies, relates to, describes, is reasonably capable of being associated with, or could reasonably be linked, directly or indirectly, with a particular consumer or household.
- **Categories (from statute):** Identifiers (name, alias, postal address, unique/online identifier, IP, email, SSN, driver’s license, passport, etc.); customer records (name, signature, SSN, address, phone, insurance, bank/card, medical); protected classifications (race, religion, sexual orientation, gender, age); biometric; internet/network activity; geolocation; sensitive PI (SSN, financial account, precise geolocation, racial/ethnic origin, religious beliefs, genetic, health, sexual orientation).
- **Entity-type mapping:** Identifiers → `PERSON_NAME`, `FIRST_NAME`, `LAST_NAME`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `POSTAL_ADDRESS`, `STREET_ADDRESS`, `CITY`, `IP_ADDRESS`, `NATIONAL_ID`, `SOCIAL_SECURITY_NUMBER`, `PASSPORT_NUMBER`, `DRIVERS_LICENSE`, `DATE_OF_BIRTH`; customer/medical → `CREDIT_CARD_NUMBER`, `BANK_ACCOUNT_NUMBER`, `FINANCIAL_ACCOUNT`, `HEALTH_INSURANCE_ID`, `DIAGNOSIS`, `MEDICATION` (CPRA); device/online → `DEVICE_ID`, `COOKIE_ID`; geolocation → `COORDINATES`, `LOCATION`; protected/sensitive → `BIOMETRIC_ID`, `RELIGION`, `ETHNICITY`, `SEXUAL_ORIENTATION`.

**References:** [California Civil Code 1798.140](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CIV&sectionNum=1798.140), [CPPA](https://cppa.ca.gov/), [oag.ca.gov/privacy/ccpa](https://oag.ca.gov/privacy/ccpa).

---

## Other built-in regulations (GDPR-style alignment)

Entity types for the following regulations are aligned with the same broad categories as GDPR (identifiers, contact, location, online identifiers, financial, health where applicable, and special categories where the law defines them). Each law defines “personal data” or “personal information” as information relating to an identified or identifiable natural person; the non-exhaustive lists in the laws are mapped to the same master entity type set.

| Regulation ID   | Name / region | Legal basis | Reference |
|-----------------|---------------|-------------|-----------|
| **uk_gdpr**     | UK GDPR / United Kingdom | Retained EU Regulation + DPA 2018; same definition as EU GDPR Art. 4(1). | [ICO UK GDPR](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/) |
| **cpra**        | California Privacy Rights Act | Cal. Civ. Code (amends CCPA); same categories as CCPA plus sensitive PI. | [oag.ca.gov/privacy/cpra](https://oag.ca.gov/privacy/cpra) |
| **pipeda**      | PIPEDA / Canada | PIPEDA **s. 2(1)**: “personal information” = information about an identifiable individual. OPC: financial, biometric, health, identifiers, opinions. | [PIPEDA s. 2](https://laws-lois.justice.gc.ca/eng/acts/P-8.6/section-2.html), [OPC](https://www.priv.gc.ca/en/privacy-topics/privacy-laws-in-canada/the-personal-information-protection-and-electronic-documents-act-pipeda/) |
| **pdpa_th**     | PDPA / Thailand | Personal Data Protection Act B.E. 2562; GDPR-influenced. | [PDPA Thailand](https://www.pdpathailand.com/) |
| **pdpa_sg**     | PDPA / Singapore | Personal Data Protection Act; PDPC. | [PDPC Singapore](https://www.pdpc.gov.sg/) |
| **appi**        | APPI / Japan | Act on the Protection of Personal Information; PPC. | [PPC Japan](https://www.ppc.go.jp/en/) |
| **pipl**        | PIPL / China | Personal Information Protection Law; CAC. | [CAC](https://www.cac.gov.cn/) |
| **popia**       | POPIA / South Africa | Protection of Personal Information Act 4 of 2013. | [POPIA](https://popia.co.za/) |
| **dpdp**        | DPDP / India | Digital Personal Data Protection Act 2023; MeitY. | [MeitY DPDP](https://www.meity.gov.in/data-protection-framework) |
| **pdpl_sa**     | PDPL / Saudi Arabia | Personal Data Protection Law (Royal Decree M/19); SDAIA. | [SDAIA](https://sdaia.gov.sa/) |
| **nzpa**        | Privacy Act 2020 / New Zealand | Privacy Act 2020; Office of the Privacy Commissioner. | [Privacy Commissioner NZ](https://www.privacy.org.nz/) |
| **australia_pa**| Privacy Act 1988 / Australia | Privacy Act 1988 (amended); OAIC. | [OAIC](https://www.oaic.gov.au/privacy/) |

For jurisdictions without a dedicated recognizer pack, the sanitizer uses the **policy composer** entity types (union of active rulesets) with the shared baseline recognizers (e.g. phone, national ID, IBAN) and NER; regulation-specific Presidio packs exist for **gdpr**, **hipaa**, and **kvkk**.

---

## How this affects the product

- **Policy composer** builds the active entity set from all active regulation rulesets (union of their `entity_types`).
- **Sanitizer** (Presidio, NER, optional Ollama layer) is asked to detect only those entity types, so masking is aligned with the regulations the user has enabled.
- **Generic labels** (e.g. the word for “address” in a language) must not be hardcoded; they can be excluded from masking via the **NonPiiRule** table (Settings/API) if desired.
