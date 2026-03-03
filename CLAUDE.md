# CLAUDE.md — Aptoflow Project Conventions

## Overview

Aptoflow is a production-grade agentic workflow development system. Claude Code builds and deploys workflows; OpenRouter (via OpenAI SDK) powers all deployed workflows on Modal.

## Python

- Python 3.12+, always use `.venv/bin/python`
- Run `python bootstrap.py` to set up the environment
- Pydantic for ALL structured data — no raw dicts for API inputs/outputs

## Project Structure

- **`lib/`** — Shared modules (client, logger, cost, models, auth, agent). Every workflow imports from here.
- **`workflows/{name}/`** — Each workflow is independent with its own `main.py`, `README.md`, `.env.example`, and tests.
- **`tests/`** — Centralized pytest infrastructure. Workflow-specific tests at `tests/test_{name}.py`.
- **`.agent/plans/`** — Implementation plans for each workflow.
- **`CATALOG.md`** — Workflow registry. Always update when creating/modifying workflows.
- **`MODELS.md`** — OpenRouter model recommendations. Consult when choosing models.

## LLM Integration

- OpenRouter via OpenAI SDK — see `lib/client.py`
- `get_client()` returns a configured OpenAI client pointing at OpenRouter
- `chat()` is the standard wrapper for all LLM calls
- Default model: `anthropic/claude-sonnet-4`
- Consult `MODELS.md` for task-specific model recommendations

## Logging

- Structured JSON logging via `lib/logger.py` — use `get_logger(name)`
- **No bare `print()` statements** in production code
- Include `workflow`, `iteration`, `cost` fields where relevant

## Safety Rules

- **Max iterations**: Default 10 for agent loops (configurable per workflow)
- **Timeouts**: Default 300s for agent loops
- **Input/output validation**: Pydantic models for all external data
- **Cost tracking**: Use `CostTracker` from `lib/cost.py` for all LLM calls
- **Rate limiting**: `RateLimiter` from `lib/auth.py` on all webhook endpoints
- **Auth**: `verify_bearer_token` from `lib/auth.py` on all webhook endpoints

## Skills

| Skill | Description |
|-------|-------------|
| `/new-workflow` | Scaffold a new workflow (name, model, deploy target) |
| `/build` | Implement a workflow from its plan |
| `/deploy` | Deploy a workflow to Modal |
| `/test` | Run tests for a specific workflow |
| `/list-workflows` | Show all workflows and their status |
| `/onboard` | Get oriented with the project |

## Key Files

- `CATALOG.md` — Workflow registry
- `MODELS.md` — Model recommendations
- `lib/` — Shared library modules
- `.agent/plans/` — Workflow implementation plans
- `bootstrap.py` — Environment setup
