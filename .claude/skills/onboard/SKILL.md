---
name: onboard
description: Get oriented with the Aptoflow project
user-invocable: true
---

# /onboard

Scan the Aptoflow project and provide a comprehensive orientation.

## Instructions

1. **Scan project structure**: List key directories and files.

2. **Read key docs**:
   - `CLAUDE.md` — Project conventions
   - `README.md` — Overview
   - `CATALOG.md` — Workflow registry
   - `MODELS.md` — Model recommendations

3. **Check each workflow**:
   - List all directories in `workflows/`
   - For each, read its README.md and check its CATALOG.md status
   - Note any workflows that are Planned but not built, or Done but not deployed

4. **Check environment**:
   - Verify `.venv` exists
   - Check if `.env` exists
   - Run a quick import check: `python -c "from lib import get_client, chat, get_logger"`

5. **Check git state**: Current branch, uncommitted changes, recent commits.

6. **Summarize**:
   - Project health overview
   - Workflow status breakdown
   - Suggested next actions (e.g., "3 workflows are Done — consider deploying with /deploy")
   - Any issues found (missing .env, broken imports, etc.)
