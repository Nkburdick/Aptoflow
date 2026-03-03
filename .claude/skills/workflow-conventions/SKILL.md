---
name: workflow-conventions
description: Reference for Aptoflow workflow conventions, structure, and requirements
user-invocable: false
---

# Workflow Conventions

This skill provides reference information for Claude when building Aptoflow workflows. It is not user-invocable.

## Directory Structure

Each workflow lives in `workflows/{name}/` with:
- `main.py` — Entry point (local CLI, Modal webhook, Modal scheduled, or combined)
- `README.md` — Workflow documentation
- `.env.example` — Required environment variables
- Tests at `tests/test_{name}.py` (centralized test directory)
- Plan at `.agent/plans/{name}.md`

## Deployment Targets

| Target | Description |
|--------|-------------|
| `local` | CLI script, runs locally |
| `modal-webhook` | Modal + FastAPI endpoint |
| `modal-scheduled` | Modal Cron job |
| `modal-combined` | Webhook + scheduled sharing `run()` |

## Modal Modes

- **Webhook**: FastAPI app on Modal with `@app.function()` and `@modal.asgi_app()`
- **Scheduled**: `@app.function(schedule=modal.Cron(...))`
- **Combined**: Both webhook and scheduled sharing a common `run()` function and `image`

## Environment Loading

```python
from dotenv import load_dotenv
load_dotenv()  # For local development
# On Modal, use modal.Secret.from_name() for secrets
```

## lib/ Usage

Every workflow MUST import from `lib/`:
- `from lib.client import chat, get_client` — LLM access
- `from lib.logger import get_logger` — Structured logging
- `from lib.cost import CostTracker, extract_cost` — Cost tracking
- `from lib.models import WorkflowInput, WorkflowOutput` — Base models (extend these)
- `from lib.auth import verify_bearer_token, RateLimiter` — Webhook auth
- `from lib.agent import run_agent_loop, ToolDefinition` — Agentic workflows

## Safety Requirements

Every workflow MUST have:
1. **Pydantic models** for all inputs and outputs
2. **Cost tracking** via `CostTracker`
3. **Structured logging** via `get_logger()` — no bare `print()`
4. **Max iterations** for any loop (default 10)
5. **Timeouts** for agent loops (default 300s)
6. **Auth + rate limiting** on webhook endpoints

## Pydantic Patterns

```python
from lib.models import WorkflowInput, WorkflowOutput

class MyInput(WorkflowInput):
    query: str
    max_results: int = 5

class MyOutput(WorkflowOutput):
    answer: str
    sources: list[str]
    cost_usd: float
```

## Testing Requirements

Every workflow needs tests at `tests/test_{name}.py`:
- Mock external API calls (use `mock_openrouter` fixture)
- Test Pydantic model validation
- Test main workflow function with sample input
- Test error handling

## Planning Rules

Before implementing a workflow, create a plan at `.agent/plans/{name}.md` using the template at `.agent/plans/_template.md`. The plan must be reviewed before implementation begins.

## Modal Mount Pattern

To make `lib/` available on Modal:

```python
import modal

image = modal.Image.debian_slim(python_version="3.12").pip_install_from_requirements("requirements.txt")
lib_mount = modal.Mount.from_local_dir("lib", remote_path="/root/lib")

app = modal.App("workflow-name", image=image, mounts=[lib_mount])
```
