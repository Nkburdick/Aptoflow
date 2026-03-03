"""Agentic tool-calling loop with safety guardrails."""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from lib.client import chat, DEFAULT_MODEL
from lib.cost import CostTracker, extract_cost
from lib.logger import get_logger

logger = get_logger("agent")


@dataclass
class ToolDefinition:
    """Definition for a tool the agent can call."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]


@dataclass
class AgentResult:
    """Result of an agent loop execution."""

    final_response: str
    iterations: int
    total_cost: float
    tool_calls_made: list[dict] = field(default_factory=list)
    timed_out: bool = False


def run_agent_loop(
    system_prompt: str,
    user_message: str,
    tools: list[ToolDefinition],
    model: str = DEFAULT_MODEL,
    max_iterations: int = 10,
    timeout_seconds: float = 300,
) -> AgentResult:
    """Execute a tool-calling agent loop with safety guardrails.

    Args:
        system_prompt: System message for the LLM.
        user_message: Initial user message.
        tools: List of available tools.
        model: OpenRouter model identifier.
        max_iterations: Maximum number of LLM calls.
        timeout_seconds: Wall-clock timeout for the entire loop.

    Returns:
        AgentResult with final response and metadata.
    """
    cost_tracker = CostTracker()
    tool_calls_made: list[dict] = []
    start_time = time.time()

    # Build tool schemas for the API
    tool_schemas = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]
    tool_handlers = {t.name: t.handler for t in tools}

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for iteration in range(1, max_iterations + 1):
        # Check timeout
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            logger.warning(
                "Agent loop timed out",
                extra={"iteration": iteration, "elapsed": elapsed},
            )
            return AgentResult(
                final_response="Agent loop timed out.",
                iterations=iteration - 1,
                total_cost=cost_tracker.total_cost_usd,
                tool_calls_made=tool_calls_made,
                timed_out=True,
            )

        logger.info(
            "Agent iteration",
            extra={"iteration": iteration, "model": model},
        )

        # Call LLM
        response = chat(
            messages=messages,
            model=model,
            tools=tool_schemas if tool_schemas else None,
        )
        cost_tracker.add(extract_cost(response))

        choice = response.choices[0]
        message = choice.message

        # If no tool calls, we're done
        if not message.tool_calls:
            final = message.content or ""
            logger.info(
                "Agent loop complete",
                extra={
                    "iterations": iteration,
                    "total_cost": cost_tracker.total_cost_usd,
                },
            )
            return AgentResult(
                final_response=final,
                iterations=iteration,
                total_cost=cost_tracker.total_cost_usd,
                tool_calls_made=tool_calls_made,
            )

        # Process tool calls
        messages.append(message.model_dump())
        for tool_call in message.tool_calls:
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)

            logger.info(
                "Executing tool",
                extra={"tool": fn_name, "iteration": iteration},
            )
            tool_calls_made.append({"name": fn_name, "arguments": fn_args})

            handler = tool_handlers.get(fn_name)
            if handler is None:
                result = json.dumps({"error": f"Unknown tool: {fn_name}"})
            else:
                try:
                    result = json.dumps(handler(**fn_args), default=str)
                except Exception as e:
                    result = json.dumps({"error": str(e)})

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

    # Exhausted iterations
    logger.warning(
        "Agent loop hit max iterations",
        extra={"max_iterations": max_iterations},
    )
    return AgentResult(
        final_response="Agent loop reached maximum iterations without completing.",
        iterations=max_iterations,
        total_cost=cost_tracker.total_cost_usd,
        tool_calls_made=tool_calls_made,
    )
