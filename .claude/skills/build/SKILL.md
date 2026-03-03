---
name: build
description: Implement a workflow from its plan
user-invocable: true
---

# /build

Implement a workflow from its `.agent/plans/` specification.

## Instructions

1. **Identify workflow**: If no workflow name is provided, check CATALOG.md for workflows with Status "Planned" or "In Progress" and ask which one to build.

2. **Read the plan**: Read `.agent/plans/{name}.md` for the full implementation spec.

3. **Read conventions**: Read `.claude/skills/workflow-conventions/SKILL.md` for project conventions.

4. **Update CATALOG.md**: Set status to "In Progress".

5. **Implement**: Follow the plan's Implementation Tasks section:
   - Define Pydantic input/output models
   - Implement the core workflow logic
   - Add structured logging throughout
   - Add cost tracking
   - Wire up the entry point (CLI, webhook, scheduled, or combined)
   - Add auth + rate limiting for webhook endpoints
   - Write tests at `tests/test_{name}.py`

6. **Validate**:
   - Run `python -c "import workflows.{name_underscored}.main"` to verify imports
   - Run `python -m pytest tests/test_{name_underscored}.py -v` to verify tests pass

7. **Update CATALOG.md**: Set status to "Done" once all tests pass.

8. **Report**: Summarize what was implemented and any decisions made.
