"""Tests for all lib/ modules."""

import json
import logging
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from lib.auth import RateLimiter, verify_bearer_token
from lib.client import get_client
from lib.cost import CostInfo, CostTracker, extract_cost
from lib.logger import JSONFormatter, get_logger
from lib.models import (
    CostRecord,
    ToolCall,
    WebhookRequest,
    WebhookResponse,
    WorkflowInput,
    WorkflowOutput,
)


# --- Client tests ---


class TestClient:
    def test_get_client_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            get_client()

    def test_get_client_returns_configured_client(self, mock_env):
        client = get_client()
        assert client.base_url.host == "openrouter.ai"


# --- Logger tests ---


class TestLogger:
    def test_json_formatter_outputs_valid_json(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_json", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert data["logger"] == "test_json"
        assert "timestamp" in data

    def test_json_formatter_includes_extras(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_extras", level=logging.INFO, pathname="", lineno=0,
            msg="step done", args=(), exc_info=None,
        )
        record.workflow = "test-wf"
        record.iteration = 3
        output = formatter.format(record)
        data = json.loads(output)
        assert data["workflow"] == "test-wf"
        assert data["iteration"] == 3

    def test_get_logger_returns_logger(self):
        log = get_logger("test_get_logger")
        assert isinstance(log, logging.Logger)
        assert log.name == "test_get_logger"


# --- Auth tests ---


class TestAuth:
    def test_valid_token(self, mock_env):
        result = verify_bearer_token("Bearer test-bearer-token")
        assert result == "test-bearer-token"

    def test_invalid_token(self, mock_env):
        with pytest.raises(HTTPException) as exc_info:
            verify_bearer_token("Bearer wrong-token")
        assert exc_info.value.status_code == 401

    def test_missing_token(self, mock_env):
        with pytest.raises(HTTPException) as exc_info:
            verify_bearer_token(None)
        assert exc_info.value.status_code == 401

    def test_missing_server_token(self, monkeypatch):
        monkeypatch.delenv("MODAL_BEARER_TOKEN", raising=False)
        with pytest.raises(HTTPException) as exc_info:
            verify_bearer_token("Bearer anything")
        assert exc_info.value.status_code == 500


class TestRateLimiter:
    def test_allows_within_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        request = MagicMock()
        request.client.host = "127.0.0.1"
        for _ in range(5):
            limiter.check(request)  # Should not raise

    def test_blocks_over_limit(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        request = MagicMock()
        request.client.host = "127.0.0.1"
        limiter.check(request)
        limiter.check(request)
        with pytest.raises(HTTPException) as exc_info:
            limiter.check(request)
        assert exc_info.value.status_code == 429


# --- Cost tests ---


class TestCost:
    def test_extract_cost_with_usage(self):
        response = MagicMock()
        response.model = "test-model"
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 50
        info = extract_cost(response)
        assert info.model == "test-model"
        assert info.total_tokens == 150

    def test_extract_cost_no_usage(self):
        response = MagicMock(spec=[])
        response.model = "test-model"
        info = extract_cost(response)
        assert info.total_tokens == 0

    def test_tracker_accumulates(self):
        tracker = CostTracker()
        tracker.add(CostInfo(model="a", total_tokens=100, cost_usd=0.01))
        tracker.add(CostInfo(model="a", total_tokens=200, cost_usd=0.02))
        tracker.add(CostInfo(model="b", total_tokens=50, cost_usd=0.005))
        assert tracker.total_tokens == 350
        assert tracker.total_cost_usd == pytest.approx(0.035)

    def test_tracker_summary(self):
        tracker = CostTracker()
        tracker.add(CostInfo(model="a", total_tokens=100, cost_usd=0.01))
        tracker.add(CostInfo(model="b", total_tokens=50, cost_usd=0.005))
        summary = tracker.summary()
        assert summary["total_calls"] == 2
        assert "a" in summary["by_model"]
        assert "b" in summary["by_model"]
        assert summary["by_model"]["a"]["calls"] == 1


# --- Agent tests ---


class TestAgent:
    @patch("lib.agent.chat")
    def test_respects_max_iterations(self, mock_chat):
        from lib.agent import ToolDefinition, run_agent_loop

        # Make chat always return a tool call to force iteration
        mock_response = MagicMock()
        tool_call = MagicMock()
        tool_call.function.name = "test_tool"
        tool_call.function.arguments = "{}"
        tool_call.id = "call_1"
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.tool_calls = [tool_call]
        mock_response.choices[0].message.content = None
        mock_response.choices[0].message.model_dump.return_value = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
        }
        mock_response.model = "test"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_chat.return_value = mock_response

        tools = [
            ToolDefinition(
                name="test_tool",
                description="A test tool",
                parameters={"type": "object", "properties": {}},
                handler=lambda: {"ok": True},
            )
        ]

        result = run_agent_loop(
            system_prompt="test",
            user_message="test",
            tools=tools,
            max_iterations=3,
        )
        assert result.iterations == 3
        assert mock_chat.call_count == 3

    @patch("lib.agent.chat")
    def test_respects_timeout(self, mock_chat):
        from lib.agent import ToolDefinition, run_agent_loop

        # Make chat sleep to trigger timeout
        def slow_chat(**kwargs):
            time.sleep(0.2)
            mock_response = MagicMock()
            tool_call = MagicMock()
            tool_call.function.name = "test_tool"
            tool_call.function.arguments = "{}"
            tool_call.id = "call_1"
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.tool_calls = [tool_call]
            mock_response.choices[0].message.content = None
            mock_response.choices[0].message.model_dump.return_value = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "test_tool", "arguments": "{}"},
                    }
                ],
            }
            mock_response.model = "test"
            mock_response.usage.prompt_tokens = 10
            mock_response.usage.completion_tokens = 5
            return mock_response

        mock_chat.side_effect = slow_chat

        tools = [
            ToolDefinition(
                name="test_tool",
                description="A test tool",
                parameters={"type": "object", "properties": {}},
                handler=lambda: {"ok": True},
            )
        ]

        result = run_agent_loop(
            system_prompt="test",
            user_message="test",
            tools=tools,
            max_iterations=100,
            timeout_seconds=0.3,
        )
        assert result.timed_out


# --- Models tests ---


class TestModels:
    def test_workflow_input_output_roundtrip(self):
        inp = WorkflowInput()
        out = WorkflowOutput()
        assert inp.model_dump() == {}
        assert out.model_dump() == {}

    def test_webhook_response_serialization(self):
        resp = WebhookResponse(
            success=True,
            result={"answer": "42"},
            cost_usd=0.01,
            model="test-model",
        )
        data = resp.model_dump()
        assert data["success"] is True
        assert data["result"]["answer"] == "42"
        assert data["cost_usd"] == 0.01

    def test_tool_call_model(self):
        tc = ToolCall(id="1", name="search", arguments={"query": "hello"})
        assert tc.name == "search"
        data = tc.model_dump()
        assert data["arguments"]["query"] == "hello"

    def test_cost_record_model(self):
        cr = CostRecord(model="test", tokens=100, cost_usd=0.01)
        data = cr.model_dump()
        assert data["model"] == "test"
        assert "timestamp" in data

    def test_webhook_request_default(self):
        req = WebhookRequest()
        assert req.input == {}
