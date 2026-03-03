"""Shared test fixtures for Aptoflow."""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def mock_env(monkeypatch):
    """Set standard test environment variables."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123")
    monkeypatch.setenv("MODAL_BEARER_TOKEN", "test-bearer-token")


@pytest.fixture()
def mock_openrouter():
    """Patch lib.client.get_client to return a mock OpenAI client."""
    mock_client = MagicMock()

    # Set up a default chat completion response
    mock_response = MagicMock()
    mock_response.model = "test-model"
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Test response"
    mock_response.choices[0].message.tool_calls = None
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5
    mock_client.chat.completions.create.return_value = mock_response

    with patch("lib.client.get_client", return_value=mock_client) as mock_get:
        yield mock_client


@pytest.fixture()
def sample_webhook_headers():
    """Sample headers for webhook testing."""
    return {
        "Authorization": "Bearer test-bearer-token",
        "Content-Type": "application/json",
    }
