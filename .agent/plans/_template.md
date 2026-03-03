# Plan: {{WORKFLOW_NAME}}

## Metadata

- **Complexity**: Simple / Medium / Complex
- **Deploy Target**: local / modal-webhook / modal-scheduled / modal-combined
- **Modal Mode**: webhook / scheduled / combined / N/A
- **Workflow Type**: single-shot / agentic / pipeline
- **Recommended Model**: `model-id`
- **Status**: Planned → In Progress → Done → Deployed

## Overview

Brief description of what this workflow does and why.

## Inputs

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| | | | |

## Outputs

| Field | Type | Description |
|-------|------|-------------|
| | | |

## Pydantic Models

```python
class Input(WorkflowInput):
    pass

class Output(WorkflowOutput):
    pass
```

## Dependencies

- External APIs or services required
- Data sources
- Any non-standard packages

## Implementation Tasks

- [ ] Define Pydantic input/output models
- [ ] Implement core logic in `run()`
- [ ] Add structured logging
- [ ] Add cost tracking
- [ ] Wire up entry point (CLI / webhook / scheduled)
- [ ] Add auth + rate limiting (webhooks only)
- [ ] Write unit tests
- [ ] Run smoke test

## Safety Configuration

| Setting | Value |
|---------|-------|
| Max iterations | 10 |
| Timeout (seconds) | 300 |
| Rate limit (req/min) | 60 |
| Cost budget (USD) | — |

## Validation

### Smoke Test
- [ ] Import check passes
- [ ] Run with sample input produces expected output shape
- [ ] Cost tracking reports non-zero values

### Unit Tests
- [ ] Pydantic model validation
- [ ] Core logic with mocked LLM
- [ ] Error handling
- [ ] Auth (webhooks only)

### Edge Cases
- [ ] Empty input
- [ ] Very long input
- [ ] Invalid input types
- [ ] API timeout

## Modal Deployment Checklist

- [ ] `modal.Secret.from_name("aptoflow-secrets")` configured
- [ ] `lib_mount` included in app
- [ ] Image has all dependencies
- [ ] Auth + rate limiting on endpoints
- [ ] Tested locally with `uvicorn` / CLI
- [ ] `modal deploy` succeeds
- [ ] Post-deploy auth tests pass (401, 422, 200)
