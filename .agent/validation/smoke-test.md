# Smoke Test Checklist

Run this checklist to validate a workflow before marking it as Done.

## Structure Check

- [ ] `workflows/{name}/main.py` exists
- [ ] `workflows/{name}/README.md` exists
- [ ] `workflows/{name}/.env.example` exists
- [ ] `tests/test_{name}.py` exists
- [ ] `.agent/plans/{name}.md` exists

## Import Check

```bash
.venv/bin/python -c "import workflows.{name_underscored}.main"
```

- [ ] Imports without errors

## Pydantic Model Check

- [ ] Input model defined and extends WorkflowInput (or BaseModel)
- [ ] Output model defined and extends WorkflowOutput (or BaseModel)
- [ ] All fields have type annotations
- [ ] Models serialize/deserialize correctly

## Dependency Check

- [ ] All imports available in `requirements.txt`
- [ ] No missing `lib/` imports
- [ ] `lib/` modules used correctly (client, logger, cost, auth)

## Pytest Execution

```bash
.venv/bin/python -m pytest tests/test_{name_underscored}.py -v
```

- [ ] All tests pass
- [ ] No warnings about missing fixtures
- [ ] Mock coverage adequate (no real API calls in tests)
