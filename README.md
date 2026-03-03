# Aptoflow

Production-grade agentic workflow development system. Build, test, and deploy AI-powered workflows to [Modal](https://modal.com) using [OpenRouter](https://openrouter.ai) for LLM access.

## Getting Started

```bash
# 1. Bootstrap the environment
python bootstrap.py

# 2. Configure environment variables
cp .env.example .env
# Edit .env with your API keys

# 3. Create your first workflow
# In Claude Code, run: /new-workflow
```

## Claude Code Skills

| Skill | Description |
|-------|-------------|
| `/new-workflow` | Scaffold a new workflow — gathers name, description, deploy target, recommends model |
| `/build` | Implement a workflow from its `.agent/plans/` spec |
| `/deploy` | Deploy a workflow to Modal with pre/post validation |
| `/test` | Run tests for a specific workflow |
| `/list-workflows` | Show all workflows with status, model, and run commands |
| `/onboard` | Get oriented — scans project structure and workflow status |

## lib/ — Shared Modules

| Module | Purpose |
|--------|---------|
| `client.py` | OpenRouter wrapper via OpenAI SDK (`get_client()`, `chat()`) |
| `agent.py` | Agentic tool-calling loop with safety guardrails |
| `logger.py` | Structured JSON logging |
| `models.py` | Pydantic base models (WorkflowInput/Output, WebhookRequest/Response) |
| `cost.py` | LLM cost tracking and aggregation |
| `auth.py` | Bearer token verification and rate limiting for webhooks |

## Safety Features

- **Max iterations** — Agent loops capped at configurable limit (default 10)
- **Timeouts** — Agent loops have wall-clock timeouts (default 300s)
- **Cost tracking** — Every LLM call tracked and summarized
- **Rate limiting** — In-memory per-IP rate limiter on webhook endpoints
- **Auth** — Bearer token verification on all webhook endpoints
- **Validation** — Pydantic models for all inputs and outputs

## Project Structure

```
Aptoflow/
├── lib/              # Shared modules
├── workflows/        # Individual workflows
├── tests/            # Centralized test suite
├── .agent/plans/     # Workflow implementation plans
├── .claude/skills/   # Claude Code skill definitions
├── CATALOG.md        # Workflow registry
├── MODELS.md         # OpenRouter model guide
└── bootstrap.py      # Environment setup
```
