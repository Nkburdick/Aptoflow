---
name: new-workflow
description: Scaffold a new Aptoflow workflow
user-invocable: true
---

# /new-workflow

Scaffold a new Aptoflow workflow with all required files and configuration.

## Instructions

1. **Read context**: Read `MODELS.md` and `.claude/skills/workflow-conventions/SKILL.md` for reference.

2. **Gather inputs** using AskUserQuestion (ask all at once where possible):
   - **Name**: Workflow name in kebab-case (e.g., `email-classifier`)
   - **Description**: One-line description of what the workflow does
   - **Deploy target**: `local`, `modal-webhook`, `modal-scheduled`, or `modal-combined`
   - **Task type**: Classification, Text Generation, Code Generation, Summarization, Data Extraction, Complex Reasoning, Multi-step Agentic, or Cheap/High Volume

3. **Recommend model**: Based on the task type, consult `MODELS.md` and recommend the appropriate model. Show the recommendation and fallback. Let the user confirm or override.

4. **Create files**:
   - `workflows/{name}/README.md` — Workflow documentation with name, description, model, deploy target, usage instructions
   - `workflows/{name}/main.py` — From the appropriate template in `.claude/skills/new-workflow/templates/`:
     - `local` → `main-local.py`
     - `modal-webhook` → `main-modal-webhook.py`
     - `modal-scheduled` → `main-modal-scheduled.py`
     - `modal-combined` → `main-modal-combined.py`
   - `workflows/{name}/.env.example` — Required env vars for this workflow
   - `tests/test_{name_underscored}.py` — Starter test file
   - `.agent/plans/{name}.md` — Plan from template at `.agent/plans/_template.md`

5. **Update CATALOG.md**: Add a row with Status: Planned, the chosen model, and other details.

6. **Report**: Show the user what was created and suggest next steps (`/build` to implement).

## Template Variables

When copying from templates, replace these placeholders:
- `{{WORKFLOW_NAME}}` — kebab-case name (e.g., `email-classifier`)
- `{{WORKFLOW_NAME_UNDERSCORED}}` — snake_case name (e.g., `email_classifier`)
- `{{WORKFLOW_DESCRIPTION}}` — one-line description
- `{{MODEL}}` — chosen OpenRouter model ID
- `{{MODAL_APP_NAME}}` — Modal app name (same as kebab-case name)
