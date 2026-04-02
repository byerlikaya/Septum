#!/usr/bin/env bash
set -euo pipefail

# Only run for git commit commands
CMD=$(jq -r '.tool_input.command // ""' 2>/dev/null)
if ! echo "$CMD" | grep -q 'git commit'; then
  exit 0
fi

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ERRORS=()

# 1. Zero-tolerance: country/language names in production code
VIOLATIONS=$(grep -rn '\bturkish\b\|\btürk\b\|\benglish\b\|\bingiliz\b' "$PROJECT_ROOT/backend/app/" \
  --include='*.py' \
  2>/dev/null \
  | grep -v '/tests/' \
  | grep -v '/national_ids/' \
  | grep -v 'database.py' \
  | grep -v '__pycache__' \
  | grep -v '^\s*#\|[[:space:]]#' \
  | grep -v '"[^"]*/' \
  || true)
if [[ -n "$VIOLATIONS" ]]; then
  ERRORS+=("[ZERO-TOLERANCE] Country/language names in production code: $VIOLATIONS")
fi

# 2. Language-specific if-branches
LANG_BRANCHES=$(grep -rn 'if.*language.*==' "$PROJECT_ROOT/backend/app/" \
  --include='*.py' \
  | grep -v '/tests/' \
  | grep -v '__pycache__' \
  | grep -v '^\s*#' \
  || true)
if [[ -n "$LANG_BRANCHES" ]]; then
  ERRORS+=("[ZERO-TOLERANCE] Language-specific branches found: $LANG_BRANCHES")
fi

# 3. CHANGELOG.md has today's date
TODAY=$(date +%Y-%m-%d)
if ! grep -q "### $TODAY" "$PROJECT_ROOT/CHANGELOG.md" 2>/dev/null; then
  ERRORS+=("[CHANGELOG] No entry for today ($TODAY) in CHANGELOG.md")
fi

# 4. No .env files staged
STAGED_ENV=$(cd "$PROJECT_ROOT" && git diff --cached --name-only 2>/dev/null | grep '^\.env' | grep -v '\.env\.example' || true)
if [[ -n "$STAGED_ENV" ]]; then
  ERRORS+=("[SECURITY] .env file staged for commit: $STAGED_ENV")
fi

# 5. README sync — if one README changed, both must change
STAGED=$(cd "$PROJECT_ROOT" && git diff --cached --name-only 2>/dev/null || true)
README_EN=$(echo "$STAGED" | grep -c '^README\.md$' || true)
README_TR=$(echo "$STAGED" | grep -c '^README\.tr\.md$' || true)
if [[ "$README_EN" -gt 0 && "$README_TR" -eq 0 ]]; then
  ERRORS+=("[README-SYNC] README.md is staged but README.tr.md is not — both must be updated together")
fi
if [[ "$README_TR" -gt 0 && "$README_EN" -eq 0 ]]; then
  ERRORS+=("[README-SYNC] README.tr.md is staged but README.md is not — both must be updated together")
fi

# 6. Regulation entity sources — if database.py entity types changed, docs must update too
DB_CHANGED=$(echo "$STAGED" | grep -c 'database\.py$' || true)
if [[ "$DB_CHANGED" -gt 0 ]]; then
  DB_DIFF=$(cd "$PROJECT_ROOT" && git diff --cached backend/app/database.py 2>/dev/null | grep -c 'entity_types' || true)
  SOURCES_CHANGED=$(echo "$STAGED" | grep -c 'REGULATION_ENTITY_SOURCES' || true)
  if [[ "$DB_DIFF" -gt 0 && "$SOURCES_CHANGED" -eq 0 ]]; then
    ERRORS+=("[REGULATION] database.py entity_types changed but REGULATION_ENTITY_SOURCES.md not updated")
  fi
fi

# 7. Dependency resolution check — when requirements.txt is staged
REQ_STAGED=$(echo "$STAGED" | grep -c 'requirements\.txt$' || true)
if [[ "$REQ_STAGED" -gt 0 ]]; then
  VENV_PYTHON="$PROJECT_ROOT/backend/.venv/bin/python"
  if [[ -x "$VENV_PYTHON" ]]; then
    DEP_CHECK=$("$VENV_PYTHON" -m pip install --dry-run -r "$PROJECT_ROOT/backend/requirements.txt" 2>&1 || true)
    if echo "$DEP_CHECK" | grep -qi 'conflicting dependencies\|ResolutionImpossible'; then
      ERRORS+=("[DEPENDENCY] pip dependency conflict detected — run: pip install --dry-run -r backend/requirements.txt")
    fi
  fi
fi

# 8. npm dependency check — when package.json is staged
PKG_STAGED=$(echo "$STAGED" | grep -c 'frontend/package\.json$' || true)
if [[ "$PKG_STAGED" -gt 0 ]]; then
  if command -v npm &>/dev/null; then
    NPM_CHECK=$(cd "$PROJECT_ROOT/frontend" && npm install --dry-run 2>&1 || true)
    if echo "$NPM_CHECK" | grep -qi 'ERESOLVE\|Could not resolve dependency'; then
      ERRORS+=("[DEPENDENCY] npm dependency conflict detected — run: cd frontend && npm install --dry-run")
    fi
  fi
fi

# Output result
if [[ ${#ERRORS[@]} -gt 0 ]]; then
  MSG=""
  for e in "${ERRORS[@]}"; do
    MSG="$MSG\n$e"
  done
  printf '{"decision":"block","reason":"Pre-commit checks failed:\\n%s"}' "$MSG"
  exit 0
fi

echo '{"systemMessage":"Pre-commit checks passed."}'
