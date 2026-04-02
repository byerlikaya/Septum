---
paths:
  - "frontend/package.json"
  - "backend/requirements.txt"
---

# Dependency Freshness Policy

- Always use the **latest stable versions** of all JavaScript/TypeScript and Python dependencies.
- When editing `package.json`, update any listed dependencies/devDependencies that have newer stable releases.
- When editing `requirements.txt`, upgrade pinned versions to the latest stable on PyPI.
- When making code changes that touch build, tooling, or infrastructure, proactively check for library updates.
- Changes must result in **0 errors, 0 warnings** after build/test.
