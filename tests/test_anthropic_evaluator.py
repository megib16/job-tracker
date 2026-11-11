import json
from unittest.mock import Mock

import pytest

from job_tracker.anthropic_evaluator import AnthropicEvaluator
from job_tracker.email_evaluator import EvaluationError
from job_tracker.main import main

from test_ingestion import message
from test_openai_agent import model_response


def test_anthropic_dry_run_uses_its_own_key_and_validates_claude_output(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai-key")
    monkeypatch.setenv("JOB_TRACKER_ANTHROPIC_MODEL", "test-claude-model")
    text = model_response(notes="Interview requested.").json()["output"][0]["content"][0]["text"]
    post = Mock(return_value=Mock(status_code=200, json=Mock(return_value={
        "stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
    })))
    monkeypatch.setattr("job_tracker.email_evaluator.requests.post", post)

    gmail = Mock()
    gmail.list.return_value.execute.return_value = {"messages": [{"id": "test"}]}
    gmail.get.return_value.execute.return_value = message(
        "Please reply to private@example.com.\nKind regards,\nRecruiter Name",
        "Your job application",
    )
    monkeypatch.setattr("job_tracker.main.load_credentials", lambda _: None)
    monkeypatch.setattr("job_tracker.main.gmail_messages", lambda _: gmail)

    assert main(["--dry-run", "--use-anthropic", "--query", "label:tracker.test", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["classified"] == 1
    args, kwargs = post.call_args
    assert args == ("https://api.anthropic.com/v1/messages",)
    assert kwargs["headers"] == {"x-api-key": "synthetic-anthropic-key", "anthropic-version": "2023-06-01"}
    payload = kwargs["json"]
    assert payload["model"] == "test-claude-model"
    assert payload["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert "private@example.com" not in payload["messages"][0]["content"]
    assert "Recruiter Name" not in payload["messages"][0]["content"]


def test_anthropic_refusal_and_http_error_are_safe(monkeypatch):
    post = Mock(return_value=Mock(status_code=200, json=Mock(return_value={
        "stop_reason": "refusal", "content": [{"type": "text", "text": "refusal"}],
    })))
    monkeypatch.setattr("job_tracker.email_evaluator.requests.post", post)
    evaluator = AnthropicEvaluator("synthetic")
    assert evaluator(message("Synthetic mail")) == ("needs_review", None)
    post.return_value = Mock(status_code=401, text="sensitive")
    with pytest.raises(EvaluationError, match="^Anthropic returned HTTP 401"):
        evaluator(message("Synthetic mail"))
