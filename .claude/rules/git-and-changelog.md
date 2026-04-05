# Git Commit & Changelog Rules

## Commit Workflow
- **Analyze and group changes** before committing — one logical change per commit.
- **Commit messages in English**, imperative style ("Add feature", "Fix bug").
- **Never push** unless explicitly asked.
- **Scan for secrets** before committing — warn and block if credentials, API keys, or private keys are detected.
- **Never commit** build artifacts, logs, `config.json`, or machine-generated files.
- **Always include a CHANGELOG.md update** in the same commit as the code change.

## Changelog Format
- Date-based sections: `### YYYY-MM-DD`. Always verify date with `date +%Y-%m-%d`.
- Group related changes as **logical development units** — one bullet per effort, not per commit.
- If a follow-up fix belongs to the same feature, append to the existing bullet instead of adding a new one.
- Format: `- **<short scope>**: <description>.`

## README Synchronization
- `README.md` (English) and `README.tr.md` (Turkish) must always have identical sections, in the same order.
- Any change to one must be mirrored to the other in the same changeset.
- Verify version numbers against `frontend/package.json` and `backend/requirements.txt`.

## Regulation Entity Sources
- `backend/docs/REGULATION_ENTITY_SOURCES.md` documents the legal basis for each regulation's entity types.
- When changing entity types for a built-in regulation, update this doc in the same commit.
