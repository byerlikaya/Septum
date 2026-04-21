#!/bin/bash
# Septum Modular Refactoring — Branch Kickstart
# Bu komutları Claude Code terminaline yapıştır

cd /Users/barisyerlikaya/Projects/Septum

# 1. Main'in güncel olduğundan emin ol
git checkout main
git pull origin main

# 2. Ana çalışma branch'ini oluştur
git checkout -b refactor/modular-architecture

# 3. Packages klasör yapısını oluştur
mkdir -p packages/{core/septum_core/{regulations,recognizers,national_ids},core/tests}
mkdir -p packages/{mcp/septum_mcp,mcp/tests}
mkdir -p packages/{api/septum_api/{routers,middleware,models},api/tests}
mkdir -p packages/{web,queue/septum_queue,queue/tests}
mkdir -p packages/{gateway/septum_gateway,gateway/tests}
mkdir -p packages/{audit/septum_audit/exporters,audit/tests}
mkdir -p docker

# 4. Her modüle __init__.py ekle
touch packages/core/septum_core/__init__.py
touch packages/core/septum_core/regulations/__init__.py
touch packages/core/septum_core/recognizers/__init__.py
touch packages/core/septum_core/national_ids/__init__.py
touch packages/mcp/septum_mcp/__init__.py
touch packages/api/septum_api/__init__.py
touch packages/api/septum_api/routers/__init__.py
touch packages/api/septum_api/middleware/__init__.py
touch packages/api/septum_api/models/__init__.py
touch packages/queue/septum_queue/__init__.py
touch packages/gateway/septum_gateway/__init__.py
touch packages/audit/septum_audit/__init__.py
touch packages/audit/septum_audit/exporters/__init__.py

# 5. İlk commit — scaffolding
git add .
git commit -m "chore: scaffold modular package structure

Create packages/ directory with 7 module skeletons:
- core: PII detection + masking engine
- mcp: MCP server for Claude Code/Desktop
- api: FastAPI REST endpoints
- web: Next.js dashboard
- queue: Cross-zone message broker
- gateway: Cloud LLM forwarder
- audit: Compliance logging

See PROJECT_SPEC.md for full architecture details."

git push -u origin refactor/modular-architecture

echo "✅ Branch created and pushed. Ready for Faz 1: septum-core extraction."
