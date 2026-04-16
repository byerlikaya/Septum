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
VIOLATIONS=$(grep -rn '\bturkish\b\|\btürk\b\|\benglish\b\|\bingiliz\b' \
  "$PROJECT_ROOT/packages/core/septum_core/" \
  "$PROJECT_ROOT/packages/api/septum_api/" \
  "$PROJECT_ROOT/packages/mcp/septum_mcp/" \
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
LANG_BRANCHES=$(grep -rn 'if.*language.*==' \
  "$PROJECT_ROOT/packages/core/septum_core/" \
  "$PROJECT_ROOT/packages/api/septum_api/" \
  "$PROJECT_ROOT/packages/mcp/septum_mcp/" \
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

# 4. No secrets files staged (.env or config.json)
# Anchor config.json to a path boundary so unrelated suffixes like
# tsconfig.json or jsconfig.json do not trip the secrets check.
STAGED_SECRETS=$(cd "$PROJECT_ROOT" && git diff --cached --name-only 2>/dev/null | grep -E '(^\.env|(^|/)config\.json$)' || true)
if [[ -n "$STAGED_SECRETS" ]]; then
  ERRORS+=("[SECURITY] Secrets file staged for commit: $STAGED_SECRETS")
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

# 6. Regulation entity sources — if a RegulationRuleset entity_types
# declaration changes, the legal-basis doc must update in the same
# commit. The awk state machine below tracks being inside a multi-line
# ``entity_types=[\n    "...", ... \n]`` block and only counts ``+``/``-``
# lines that add or remove a quoted uppercase PII type inside such a
# block. Inline placeholders like NonPiiRule's ``entity_types=[]`` and
# unrelated uppercase string literals (env var names in ``os.getenv``
# calls, SQL keywords, etc.) stay untouched. ``git diff -U999`` expands
# the hunk to the full file so the block opening line is always
# visible, otherwise the state machine would miss mid-block edits.
# Both the legacy ``backend/app`` path and the new
# ``packages/api/septum_api`` location are covered so file moves
# between the two do not slip the check.
SEED_CHANGED=$(echo "$STAGED" | grep -cE '(seeds/regulations|database)\.py$' || true)
if [[ "$SEED_CHANGED" -gt 0 ]]; then
  SEED_DIFF=$(cd "$PROJECT_ROOT" && git diff --cached -U999 \
      -- '*seeds/regulations.py' '*database.py' 2>/dev/null \
    | awk '
        /^[ +-][[:space:]]*entity_types[[:space:]]*=[[:space:]]*\[[[:space:]]*$/ { in_block=1; next }
        in_block && /^[ +-][[:space:]]*\][[:space:]]*,?[[:space:]]*$/ { in_block=0; next }
        in_block && /^[+-][[:space:]]*"[A-Z][A-Z_]+"/ { count++ }
        END { print count+0 }
      ')
  SOURCES_CHANGED=$(echo "$STAGED" | grep -c 'REGULATION_ENTITY_SOURCES' || true)
  if [[ "$SEED_DIFF" -gt 0 && "$SOURCES_CHANGED" -eq 0 ]]; then
    ERRORS+=("[REGULATION] RegulationRuleset entity_types changed but REGULATION_ENTITY_SOURCES.md not updated")
  fi
fi

# 7. Dependency resolution check — when requirements.txt is staged
REQ_STAGED=$(echo "$STAGED" | grep -c 'requirements\.txt$' || true)
if [[ "$REQ_STAGED" -gt 0 ]]; then
  # Prefer a system python with pip; the optional local venv under
  # packages/api/.venv is consulted if present.
  VENV_PYTHON="$PROJECT_ROOT/packages/api/.venv/bin/python"
  [[ -x "$VENV_PYTHON" ]] || VENV_PYTHON="$(command -v python3 || true)"
  if [[ -x "$VENV_PYTHON" ]]; then
    DEP_CHECK=$("$VENV_PYTHON" -m pip install --dry-run -r "$PROJECT_ROOT/packages/api/requirements.txt" 2>&1 || true)
    if echo "$DEP_CHECK" | grep -qi 'conflicting dependencies\|ResolutionImpossible'; then
      ERRORS+=("[DEPENDENCY] pip dependency conflict detected — run: pip install --dry-run -r packages/api/requirements.txt")
    fi
  fi
fi

# 8. npm dependency check — when packages/web/package.json is staged
PKG_STAGED=$(echo "$STAGED" | grep -c 'packages/web/package\.json$' || true)
if [[ "$PKG_STAGED" -gt 0 ]]; then
  if command -v npm &>/dev/null; then
    NPM_CHECK=$(cd "$PROJECT_ROOT/packages/web" && npm install --dry-run 2>&1 || true)
    if echo "$NPM_CHECK" | grep -qi 'ERESOLVE\|Could not resolve dependency'; then
      ERRORS+=("[DEPENDENCY] npm dependency conflict detected — run: cd packages/web && npm install --dry-run")
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
