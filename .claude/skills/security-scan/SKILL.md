---
name: security-scan
description: Technology-agnostic security scan for the Septum codebase. Detects stack, audits dependencies, scans code and configuration against OWASP Top 10 and additional risks, and produces a severity-sorted report with concrete fixes. Trigger phrases: "security scan", "guvenlik taramasi", "OWASP check", "vulnerability scan", "zafiyet tara", "find security issues", "audit security".
---

# Security Scan for Septum

This skill performs a comprehensive, technology-agnostic security audit of the Septum project: it detects the stack (Python/FastAPI backend and Next.js/TypeScript frontend), audits dependencies, scans the code for vulnerabilities (OWASP Top 10 and beyond), reviews configuration, and generates a severity-sorted report with actionable fixes.

The scan is **advisory, not exhaustive**; it is not a replacement for professional penetration testing or dedicated SAST/DAST tooling.

---

## Pre-Scan Checklist

Track these items during the scan:

- [ ] Project language(s), framework(s), and package managers detected
- [ ] Dependency audit completed for each detected ecosystem
- [ ] Code scanned for all applicable OWASP Top 10 categories
- [ ] Configuration files checked for security anti-patterns
- [ ] Report generated with severity-sorted findings
- [ ] Fix options presented to user
- [ ] No full secrets exposed in report output (only masked)

---

## Step 1: Project Analysis (Septum-Aware, Tech-Agnostic)

1. **Detect ecosystems and frameworks**:
   - Look for manifest/build files:
     - Backend (Python): `packages/api/requirements.txt`, `packages/api/pyproject.toml` (if present), `packages/api/septum_api/main.py`
     - Frontend (Node/TypeScript): `packages/web/package.json`, `frontend/package-lock.json`, `frontend/pnpm-lock.yaml`, `frontend/yarn.lock`, `frontend/src/app/layout.tsx`
     - Container/infra: `Dockerfile`, `docker/api.Dockerfile`, `docker/web.Dockerfile`, `docker-compose.yml`, CI configs (if present).
   - Infer:
     - Backend: Python + FastAPI + Septum-specific privacy/PII stack.
     - Frontend: Next.js + React + TypeScript + Tailwind + shadcn/ui.

2. **Record all detected stacks and package managers**:
   - Python with `pip`/`pip-tools` (backend).
   - Node with `npm`/`pnpm`/`yarn` (frontend) — infer from lockfile presence.

3. **Use web search to refresh current best practices**:
   - `"OWASP Top 10 latest"`
   - `"FastAPI security best practices"`
   - `"Next.js security best practices"`
   - `"npm security audit latest recommended"`, `"pip dependency vulnerability scan 2026"`.

4. **Store analysis context**:
   - For each ecosystem: language, framework, package manager, root directory, and main entrypoints.

---

## Step 2: Dependency Audit

For **each** detected ecosystem:

1. **Identify package manager and audit tool**:
   - Python:
     - Prefer `pip-audit` (or latest recommended tool from web search).
   - Node (frontend):
     - Use the package manager-consistent audit:
       - `npm audit --json` for npm.
       - `pnpm audit --json` for pnpm.
       - Yarn: follow current recommended approach (via web search).

2. **Confirm presence or request installation**:
   - If an audit tool (`pip-audit`, etc.) is not installed:
     - **Do not install automatically.**
     - Inform the user which tool is recommended and the exact install command (e.g., `pip install pip-audit`).
     - Ask explicit permission before installing.

3. **Run audit commands in the correct working directory**:
   - Backend: run from `packages/api/`.
   - Frontend: run from `frontend/`.
   - Use JSON or machine-readable output where available (e.g., `--json` flags).

4. **Parse and normalize results**:
   - For each finding, capture:
     - Ecosystem (Python/Node)
     - Package name
     - Current version
     - Vulnerability ID (CVE or advisory ID)
     - Severity (map vendor-specific severities into: CRITICAL, HIGH, MEDIUM, LOW)
     - Fixed version (if provided)
     - Direct vs transitive (if provided)

5. **Attach dependency findings to the final report** in the Dependency section, cross-referenced with code/config implications where possible.

---

## Step 3: Code Scan — Security Vulnerability Analysis

Use OWASP Top 10 (latest version) as the mandatory baseline and extend beyond it when Septum-specific risks are discovered (e.g., anonymization logic, encryption, PII handling).

### 3.1 Preparation

1. Use web search for:
   - `"OWASP Top 10 latest"`
   - `"FastAPI common security vulnerabilities"`
   - `"Next.js security vulnerabilities and best practices"`
   - `"Python security pitfalls injection XSS"`

2. Identify **entrypoints and sensitive modules** in Septum:
   - Backend:
     - API routers in `packages/api/septum_api/routers/` (`documents.py`, `chat.py`, `approval.py`, `settings.py`, `regulations.py`, etc.).
     - Services under `packages/api/septum_api/services/` (sanitizer, anonymization, crypto, ingestion, national_ids, policy_composer, vector_store, llm_router, deanonymizer).
     - Utility modules in `packages/api/septum_api/utils/` (especially `crypto.py`, `device.py`, `logger.py`, `text_utils.py`).
   - Frontend:
     - Next.js app routes under `frontend/src/app/`.
     - API client logic in `frontend/src/lib/api.ts`, `frontend/src/lib/uploadDocuments.ts`.
     - Global stores in `frontend/src/store/`.

3. Identify **user input sources**:
   - HTTP JSON bodies, query params, headers, file uploads (especially documents/audio/images).
   - Web forms and chat inputs on the frontend.
   - Any CLI tools or scripts if present.

### 3.2 Vulnerability Categories to Check

For each category, trace data flow from user input → processing → persistence → external services. Verify sanitization, validation, and access control.

#### Injection Vulnerabilities

- **SQL Injection / ORM misuse**:
  - Check all data access code (e.g., SQLAlchemy in `database.py`, any raw SQL).
  - Flag any string-concatenated SQL or format strings using user input.
  - Ensure parameterized queries or ORM query builders are used.

- **Command Injection**:
  - Search backend for calls to `subprocess`, `os.system`, `Popen`, ffmpeg wrappers, Whisper/audio processing, LibreOffice, or other external binaries.
  - Flag any direct concatenation of user input into shell commands; ensure safe argument lists and explicit whitelisting.

- **NoSQL / Expression / Template Injection**:
  - If present (e.g., dynamic expression evaluation), verify that:
    - No `eval`, `exec`, or equivalent on untrusted input.
    - No template engines render unescaped user-supplied templates.

#### Cross-Site Vulnerabilities

- **XSS (Cross-Site Scripting)**:
  - In the frontend, review:
    - Any `dangerouslySetInnerHTML`, raw HTML rendering, or markdown rendering.
    - Chat messages, document previews, and JSON output panels.
  - Ensure user-originated text is escaped/sanitized before rendering, especially in any HTML injection points.

- **CSRF (Cross-Site Request Forgery)**:
  - For state-changing backend endpoints (upload, delete, settings changes, approvals), ensure:
    - Either stateless token-based auth (e.g., Authorization headers) or CSRF tokens are in use if cookies are used.
    - If Septum is purely API-token based (no browser cookies), document this and treat classic CSRF as non-applicable.

- **CORS Misconfiguration**:
  - Inspect any CORS middleware configuration in FastAPI.
  - Flag:
    - `allow_origins=["*"]` combined with `allow_credentials=True`.
    - Overly permissive allowed methods/headers without justification.

#### Data Exposure & PII Handling

- **Hardcoded Secrets**:
  - Conceptually search backend and frontend for secrets using regex patterns:
    - `password\s*=\s*["'][^"']+["']`
    - `api[_-]?key\s*=\s*["'][^"']+["']`
    - `secret\s*=\s*["'][^"']+["']`
    - `-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----`
  - Exclude `.env.example` and explicit placeholder values.
  - In the report, **mask secrets**: show only first 4 and last 4 characters (e.g., `sk-p...i789`).

- **Sensitive Data in Logs**:
  - Inspect `logger.py` usage and any `print`/logging calls.
  - Ensure:
    - No raw PII, anonymization maps, tokens, or decrypted texts are logged.
    - Logs contain only metadata, IDs, and non-sensitive context.

- **Information Disclosure**:
  - Check error handling in routers and services:
    - Avoid returning stack traces, internal file paths, or detailed exceptions to clients.
    - Return normalized, user-friendly error messages.

#### File & Path Vulnerabilities

- **Path Traversal**:
  - Inspect file upload, storage, and preview logic:
    - Backend document ingestion, encrypted file paths, temporary directories.
  - Ensure:
    - User-supplied filenames are not used directly to construct filesystem paths.
    - Paths are normalized and constrained to specific base directories.

- **Unrestricted File Upload**:
  - For `/api/documents/upload` and related routes:
    - Enforce `MAX_FILE_SIZE_MB` from config.
    - Validate content type using python-magic (as per Septum architecture).
    - Disallow executable uploads or ensure they are never executed and only stored encrypted.

#### Deserialization & Data Handling

- **Insecure Deserialization**:
  - Check any usage of pickling, untrusted JSON/YAML/XML deserialization:
    - For YAML, ensure safe loaders are used for untrusted input.
    - Avoid pickle for untrusted data altogether.

- **Mass Assignment**:
  - For Pydantic models and ORMs, ensure:
    - Only explicit fields are accepted.
    - No catch-all dynamic field binding from request bodies into database models.

#### Authentication & Authorization

- **Broken Authentication**:
  - Identify how Septum authenticates:
    - API keys, tokens, or auth middleware (if present).
  - Check:
    - Password handling (if Septum has user accounts).
    - Rate limiting on authentication endpoints.
    - Secure storage of credentials (hashed with strong algorithms).

- **Broken Access Control / IDOR**:
  - For each router (`documents`, `chunks`, `chat`, `approval`, `settings`, `regulations`):
    - Verify that sensitive endpoints require authentication and authorization.
    - Ensure users cannot access documents, chunks, or settings they do not own.
    - Check for any direct object references using IDs in URLs or bodies without ownership checks.

- **Admin / Privileged Areas**:
  - If there is any admin-like functionality (e.g., global settings, regulations management):
    - Ensure role/permission checks are implemented and consistently applied.
    - Check for privilege escalation risks (e.g., users able to set their own role).

- **JWT or Token Issues** (if used):
  - Verify:
    - Strong signing keys and algorithms.
    - Short-lived tokens with expiration and rotation.
    - No `alg: none` or symmetric/asymmetric confusion.

#### Infrastructure & Configuration

- **Security Headers**:
  - Inspect how frontend and backend are served (e.g., via reverse proxy, Next.js config, FastAPI middlewares).
  - Ensure:
    - Use of common security headers: CSP, X-Frame-Options, HSTS, X-Content-Type-Options, Referrer-Policy.
    - If headers are delegated to a proxy (Nginx, etc.), document this assumption.

- **Debug Mode in Production**:
  - Check FastAPI/uvicorn settings:
    - Ensure `debug=True` is not used for production deployments.
  - For Next.js:
    - Ensure production builds are used in production.
    - Avoid exposing React DevTools or verbose debug info.

---

## Step 4: Configuration Scan

Focus on project and deployment configuration files:

1. **Environment Files**:
   - Check whether `.env` is tracked by git (conceptually via `git ls-files --error-unmatch .env`).
   - Confirm `.env` (and variants) are in `.gitignore`.
   - For committed secrets, recommend:
     - `git rm --cached .env`
     - Rotate all exposed secrets (DB, API keys, encryption keys).

2. **Debug / Development Flags**:
   - Search in backend and frontend configs for:
     - `DEBUG`, `ENV=development`, `LOG_LEVEL=DEBUG`, `REQUIRE_APPROVAL_DEFAULT=false` used in production.
   - Use web search with `"FastAPI debug mode production security"` and `"Next.js production security config"` for current guidance.

3. **Insecure HTTP**:
   - Search code and configs for `http://` URLs:
     - Flag non-localhost targets.
     - Exclude `http://localhost`, `http://127.0.0.1`, `http://0.0.0.0`.
   - Check cookie and session options:
     - `secure: false` for cookies in production.
     - `SameSite=None` without `Secure`.

4. **Encryption & Keys (Septum-Specific)**:
   - Inspect `crypto.py`:
     - Verify AES-256-GCM is used with random nonces and separate keys.
     - Ensure encryption keys are never hardcoded and only loaded from environment or secure storage.
   - Verify:
     - Files are encrypted at rest according to project rules.
     - Decryption happens only in-memory for previews.

---

## Step 5: Report Format

After completing analysis, compile **one consolidated report**, sorted by severity: CRITICAL, HIGH, MEDIUM, LOW, INFO.

For each finding, use this structure:

```text
[SEVERITY] Title — relative/path/to/file.ext:LINE
  Issue: Clear explanation of what is wrong in this specific code/config.
  Impact: What an attacker could do or what risk this creates.
  Fix: Concrete steps or code changes for this project (Septum), not generic advice.
```

Examples:

```text
[CRITICAL] Hardcoded encryption key — packages/api/septum_api/utils/crypto.py:120
  Issue: A symmetric encryption key is hardcoded in source code.
  Impact: If the repository is leaked, an attacker can decrypt all stored documents.
  Fix: Load the key from an environment variable, ensure .env is not committed, and rotate the key.

[HIGH] Known vulnerability in fastapi@X.Y.Z — packages/api/requirements.txt
  Issue: pip-audit reports CVE-2025-12345 affecting this version of FastAPI.
  Impact: Remote attackers may bypass authentication on certain routes.
  Fix: Upgrade fastapi to the minimum fixed version suggested by pip-audit and run the test suite.

[MEDIUM] CORS misconfiguration — packages/api/septum_api/main.py:45
  Issue: CORS middleware allows all origins with credentials enabled.
  Impact: Any website can make authenticated requests to this API from a victim's browser.
  Fix: Restrict `allow_origins` to an explicit list of trusted domains or disable credentials for wildcard origins.

[LOW] Missing security headers — docker-compose.yml:20
  Issue: HTTP stack does not configure HSTS or X-Frame-Options.
  Impact: Increases exposure to clickjacking and protocol downgrade risks.
  Fix: Add appropriate security headers via reverse proxy or application middleware.
```

Finish with a summary:

```text
=== Security Scan Summary ===
  CRITICAL: X
  HIGH:     X
  MEDIUM:   X
  LOW:      X
  INFO:     X
  Total:    X findings
```

Also clearly state:

- That this scan is advisory and not exhaustive.
- That no raw secrets are shown in the report (only masked previews).

---

## Step 6: Fix Offering Workflow

After presenting the report, offer the user options:

```text
How would you like to proceed?
  1. Fix all — propose fixes for all findings.
  2. Critical only — focus on CRITICAL and HIGH severity issues first.
  3. One-by-one — review and approve each fix individually.
  4. Manual — user will handle fixes; no code changes.
```

For any option involving changes:

1. **Never modify files without explicit confirmation.**
2. For each finding to fix:
   - Propose a concrete patch (diff-style explanation).
   - Explain any behavior impact (if any).
   - Ask for approval to apply the change.
3. For dependency upgrades:
   - Show:
     - Current version.
     - Target version.
     - Relevant breaking change notes from release docs (via web search).
   - After upgrade:
     - Re-run the corresponding audit tool to confirm the vulnerability is resolved.

---

## Step 7: Global Safety Rules

These constraints apply throughout the entire scan:

1. **Advisory, not exhaustive**:
   - Always remind the user that this is not a substitute for professional penetration testing or specialized security tooling.

2. **Mask secrets**:
   - If a hardcoded secret is detected, display only a masked form (first 4 + last 4 chars).

3. **No automatic fixes**:
   - All fixes require explicit user confirmation before modifying any file.

4. **No tool installation without consent**:
   - Ask before installing any additional audit or security tools.

5. **Preserve functionality**:
   - Fixes must be minimal and focused on closing security gaps.
   - Warn the user when there is any risk of behavior change and suggest regression testing.

6. **No exfiltration of code or data**:
   - Do not send project code or sensitive data to external APIs or services during analysis.
   - Use web search only for general guidance, never for uploading project code.

7. **Scope to project boundaries**:
   - Only scan files within the Septum project directory.
   - Do not access system files or other unrelated projects.

