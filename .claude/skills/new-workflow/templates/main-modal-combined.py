"""{{WORKFLOW_NAME}} — {{WORKFLOW_DESCRIPTION}}

Modal combined deployment: webhook + scheduled, sharing run() and image.
"""

import sys

import modal
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, Request
from pydantic import BaseModel

load_dotenv()

# --- Modal setup (shared) ---

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


# --- Core logic (shared between webhook and scheduled) ---


def run(input_data: Input) -> Output:
    """Execute the workflow — called by both webhook and scheduled."""
    sys.path.insert(0, "/root")

    from lib.client import chat
    from lib.cost import CostTracker, extract_cost
    from lib.logger import get_logger

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


# --- Scheduled function ---

# TODO: Adjust cron schedule as needed (this runs daily at midnight UTC)
@app.function(
    schedule=modal.Cron("0 0 * * *"),
    secrets=[modal.Secret.from_name("aptoflow-secrets")],
)
def scheduled_run():
    """Scheduled run with default input."""
    input_data = Input(query="TODO: Default scheduled input")
    output = run(input_data)
    return output.model_dump()


# --- Local testing ---

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(web_app, host="0.0.0.0", port=8000)
