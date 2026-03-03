"""{{WORKFLOW_NAME}} — {{WORKFLOW_DESCRIPTION}}

Local CLI workflow.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

# Add project root to path for lib/ imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.client import chat
from lib.cost import CostTracker, extract_cost
from lib.logger import get_logger
from lib.models import WorkflowInput, WorkflowOutput

load_dotenv()

logger = get_logger("{{WORKFLOW_NAME}}")
MODEL = "{{MODEL}}"


# --- Pydantic models ---


class Input(WorkflowInput):
    """TODO: Define input fields."""

    query: str


class Output(WorkflowOutput):
    """TODO: Define output fields."""

    answer: str
    cost_usd: float


# --- Core logic ---


def run(input_data: Input) -> Output:
    """Execute the workflow."""
    tracker = CostTracker()

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


# --- CLI entry point ---

if __name__ == "__main__":
    import json

    sample_input = Input(query="Hello, world!")
    result = run(sample_input)
    print(json.dumps(result.model_dump(), indent=2))
