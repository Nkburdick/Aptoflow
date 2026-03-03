---
name: test
description: Run tests for a specific workflow
user-invocable: true
---

# /test

Run tests for a specific Aptoflow workflow.

## Instructions

1. **Identify workflow**: If no workflow name is provided, ask which workflow to test. Accept either kebab-case or snake_case names.

2. **Run pytest**:
   ```bash
   .venv/bin/python -m pytest tests/test_{name_underscored}.py -v
   ```

3. **Run smoke test**: Check if `.agent/plans/{name}.md` has a Validation section with smoke test instructions. If so, execute them.

4. **Run import check**:
   ```bash
   .venv/bin/python -c "from workflows.{name_underscored} import main"
   ```

5. **Report**: Show pass/fail results, any failures with details, and overall status.

## Running All Tests

If the user asks to test everything:
```bash
.venv/bin/python -m pytest tests/ -v
```
