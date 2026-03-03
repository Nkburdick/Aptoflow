"""OpenRouter LLM client via OpenAI SDK."""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DEFAULT_MODEL = "anthropic/claude-sonnet-4"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_client() -> OpenAI:
    """Create an OpenAI client configured for OpenRouter.

    Returns:
        Configured OpenAI client.

    Raises:
        ValueError: If OPENROUTER_API_KEY is not set.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is required")

    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        default_headers={
            "HTTP-Referer": "https://github.com/aptoflow",
            "X-Title": "Aptoflow",
        },
    )


def chat(
    messages: list[dict],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    tools: list[dict] | None = None,
    **kwargs,
):
    """Send a chat completion request via OpenRouter.

    Args:
        messages: List of message dicts (role, content).
        model: OpenRouter model identifier.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens in response.
        tools: Optional tool definitions for function calling.
        **kwargs: Additional arguments passed to the API.

    Returns:
        OpenAI ChatCompletion response object.
    """
    client = get_client()
    params = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        **kwargs,
    }
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if tools is not None:
        params["tools"] = tools

    return client.chat.completions.create(**params)
