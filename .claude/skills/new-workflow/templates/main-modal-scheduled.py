"""{{WORKFLOW_NAME}} — {{WORKFLOW_DESCRIPTION}}

Modal scheduled (cron) deployment.
"""

import sys

import modal
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

# --- Modal setup ---

image = modal.Image.debian_slim(python_version="3.12").pip_install_from_requirements("requirements.txt")
lib_mount = modal.Mount.from_local_dir("lib", remote_path="/root/lib")
app = modal.App("{{MODAL_APP_NAME}}", image=image, mounts=[lib_mount])


# --- Pydantic models ---


class Output(BaseModel):
    """TODO: Define output fields."""

    result: str
    cost_usd: float


# --- Core logic ---


def run() -> Output:
    """Execute the scheduled workflow."""
    sys.path.insert(0, "/root")

    from lib.client import chat
    from lib.cost import CostTracker, extract_cost
    from lib.logger import get_logger

    logger = get_logger("{{WORKFLOW_NAME}}")
    tracker = CostTracker()
    MODEL = "{{MODEL}}"

    logger.info("Starting scheduled run", extra={"workflow": "{{WORKFLOW_NAME}}"})

    response = chat(
        messages=[
            {"role": "system", "content": "TODO: System prompt"},
            {"role": "user", "content": "TODO: Scheduled task input"},
        ],
        model=MODEL,
    )
    tracker.add(extract_cost(response))

    result = response.choices[0].message.content or ""

    logger.info("Scheduled run complete", extra={"workflow": "{{WORKFLOW_NAME}}", "cost": tracker.summary()})

    return Output(result=result, cost_usd=tracker.total_cost_usd)


# --- Modal scheduled function ---

# TODO: Adjust cron schedule as needed (this runs daily at midnight UTC)
@app.function(
    schedule=modal.Cron("0 0 * * *"),
    secrets=[modal.Secret.from_name("aptoflow-secrets")],
)
def scheduled_run():
    output = run()
    return output.model_dump()


# --- Local testing ---

if __name__ == "__main__":
    import json

    result = run()
    print(json.dumps(result.model_dump(), indent=2))
