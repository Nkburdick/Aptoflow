"""{{WORKFLOW_NAME}} — {{WORKFLOW_DESCRIPTION}}

Modal webhook deployment.
"""

import sys
from pathlib import Path

import modal
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, Request
from pydantic import BaseModel

load_dotenv()

# --- Modal setup ---

image = modal.Image.debian_slim(python_version="3.12").pip_install_from_requirements("requirements.txt")
lib_mount = modal.Mount.from_local_dir("lib", remote_path="/root/lib")
app = modal.App("{{MODAL_APP_NAME}}", image=image, mounts=[lib_mount])

# --- FastAPI app ---

web_app = FastAPI(title="{{WORKFLOW_NAME}}")


# --- Pydantic models ---


class Input(BaseModel):
    """TODO: Define input fields."""

    query: str


class Output(BaseModel):
    """TODO: Define output fields."""

    answer: str
    cost_usd: float


# --- Core logic ---


def run(input_data: Input) -> Output:
    """Execute the workflow."""
    sys.path.insert(0, "/root")

    from lib.client import chat
    from lib.cost import CostTracker, extract_cost
    from lib.logger import get_logger
    from lib.models import WebhookResponse

    logger = get_logger("{{WORKFLOW_NAME}}")
    tracker = CostTracker()
    MODEL = "{{MODEL}}"

    logger.info("Starting workflow", extra={"workflow": "{{WORKFLOW_NAME}}", "input": input_data.model_dump()})

    response = chat(
        messages=[
            {"role": "system", "content": "TODO: System prompt"},
            {"role": "user", "content": input_data.query},
        ],
        model=MODEL,
    )
    tracker.add(extract_cost(response))

    answer = response.choices[0].message.content or ""

    logger.info("Workflow complete", extra={"workflow": "{{WORKFLOW_NAME}}", "cost": tracker.summary()})

    return Output(answer=answer, cost_usd=tracker.total_cost_usd)


# --- Webhook endpoint ---


@web_app.post("/run")
async def handle_run(
    input_data: Input,
    request: Request,
    authorization: str | None = Header(None),
):
    """Webhook endpoint with auth and rate limiting."""
    sys.path.insert(0, "/root")

    from lib.auth import RateLimiter, verify_bearer_token
    from lib.models import WebhookResponse

    verify_bearer_token(authorization)
    rate_limiter = RateLimiter(max_requests=60, window_seconds=60)
    rate_limiter.check(request)

    try:
        result = run(input_data)
        return WebhookResponse(
            success=True,
            result=result.model_dump(),
            cost_usd=result.cost_usd,
            model="{{MODEL}}",
        )
    except Exception as e:
        return WebhookResponse(success=False, error=str(e))


@app.function(secrets=[modal.Secret.from_name("aptoflow-secrets")])
@modal.asgi_app()
def fastapi_app():
    return web_app


# --- Local testing ---

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(web_app, host="0.0.0.0", port=8000)
