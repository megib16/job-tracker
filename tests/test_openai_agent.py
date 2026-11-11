from datetime import datetime, timezone
import json
from unittest.mock import Mock

import pytest

from job_tracker.agent import run_agent
from job_tracker.main import main
from job_tracker.email_evaluator import EvaluationError
from job_tracker.openai_evaluator import OpenAIEvaluator
from job_tracker.store import list_applications

from test_ingestion import message


def model_response(**fields):
    output = {
        "outcome": "application", "company": "Example Mobility", "position": "Data Analyst",
        "job_id": "ABC-12", "status": "applied", "application_date": None, "rejection_date": None,
        "notes": None,
    }
    output.update(fields)
    return Mock(status_code=200, json=Mock(return_value={
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(output)}]}],
    }))


def test_evaluator_sends_clean_text_and_validates_note(monkeypatch):
    api = Mock(return_value=model_response(notes="Requested a transcript."))
    monkeypatch.setattr("job_tracker.email_evaluator.requests.post", api)
    email = message(
        "Hi Jane Example,\nPlease supply your transcript. Contact private@example.com "
        "or +49 151 555 12345. https://example.com/personal\n"
        "Kind regards,\nRecruiter Name\n> quoted private content",
        "Application update for private@example.com",
    )
    email.update(internalDate="1780272000000", labelIds=["INBOX"])
    outcome, record = OpenAIEvaluator("synthetic-key")(email)
    assert outcome == "classified"
    assert record.notes == "Requested a transcript."
    payload = api.call_args.kwargs["json"]
    assert payload["store"] is False
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["additionalProperties"] is False
    evidence = json.loads(payload["input"])
    assert evidence["gmail_received_at_ms"] == "1780272000000"
    assert evidence["direction"] == "received"
    assert "[redacted]" in evidence["body"]
    for private in ("private@example.com", "Jane Example", "Recruiter Name", "quoted private content", "+49", "https://"):
        assert private not in payload["input"]


@pytest.mark.parametrize(("fields", "expected"), [
    ({"outcome": "unrelated"}, "no_signal"),
    ({"outcome": "uncertain"}, "needs_review"),
    ({"company": None}, "needs_review"),
    ({"notes": "Contact me at private@example.com."}, "needs_review"),
    ({"application_date": "yesterday"}, "needs_review"),
    ({"status": "rejected", "application_date": "2026-11-05", "rejection_date": "2026-10-01"}, "needs_review"),
])
def test_ambiguous_or_invalid_results_never_become_rows(monkeypatch, fields, expected):
    monkeypatch.setattr("job_tracker.email_evaluator.requests.post", Mock(return_value=model_response(**fields)))
    assert OpenAIEvaluator("synthetic-key")(message("Synthetic message")) == (expected, None)


def test_ai_backfill_links_sent_application_to_rejection_and_preserves_other_tabs(tmp_path, monkeypatch):
    api = Mock(side_effect=[
        model_response(status="rejected", notes="Employer declined this application."),
        model_response(),
        model_response(company="Example Rail", position="Engineer", job_id=None, status="interview"),
        model_response(outcome="unrelated"),
    ])
    monkeypatch.setattr("job_tracker.email_evaluator.requests.post", api)
    gmail = Mock()
    gmail.list.return_value.execute.return_value = {"messages": [{"id": str(n)} for n in range(4)]}
    gmail.get.return_value.execute.side_effect = [
        {**message("We won't be able to offer you this vacancy."),
         "internalDate": "1780272000000", "labelIds": ["INBOX"]},
        {**message("I am submitting my application for the role of Data Analyst."),
         "internalDate": "1778976000000", "labelIds": ["SENT"]},
        {**message("We would like to meet next Tuesday."),
         "internalDate": "1780272000000", "labelIds": ["INBOX"]},
        {**message("Weekly food specials."), "internalDate": "1780272000000", "labelIds": ["INBOX"]},
    ]
    sheets = Mock()
    sheets.get.return_value.execute.return_value = {
        "sheets": [
            {"properties": {"title": "Applications", "sheetId": 2}},
            {"properties": {"title": "My Notes", "sheetId": 3}},
        ],
    }
    db = tmp_path / "agent.sqlite"
    result = run_agent(gmail, sheets, "sheet", db, evaluator=OpenAIEvaluator("synthetic-key"),
                       now=datetime(2026, 6, 2, tzinfo=timezone.utc))
    assert result["sheet_rows"] == 2
    assert result["no_signal"] == 1
    assert " -in:sent" not in gmail.list.call_args.kwargs["q"]
    assert "application" not in gmail.list.call_args.kwargs["q"]
    assert gmail.get.call_args.kwargs["fields"] == "payload,internalDate,labelIds"
    assert json.loads(api.call_args_list[1].kwargs["json"]["input"])["direction"] == "sent"
    application = list_applications(db)[0]
    assert application.status.value == "rejected"
    assert application.application_date.isoformat() == "2026-05-17"
    assert application.rejection_date.isoformat() == "2026-06-01"
    requests = sheets.batchUpdate.call_args.kwargs["body"]["requests"]
    new_tab = requests[0]["addSheet"]["properties"]
    assert new_tab["title"] == "AI Applications"
    assert requests[1]["updateCells"]["range"]["sheetId"] == 4
    notes_cell = requests[1]["updateCells"]["rows"][1]["values"][-1]
    assert notes_cell["userEnteredValue"] == {"stringValue": "Employer declined this application."}


def test_api_http_failure_is_sanitized_and_missing_key_requires_no_google_call(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["--dry-run", "--use-openai"]) == 1
    assert "Set OPENAI_API_KEY" in capsys.readouterr().err
    response = Mock(status_code=429, text="sensitive")
    monkeypatch.setattr("job_tracker.email_evaluator.requests.post", Mock(return_value=response))
    with pytest.raises(EvaluationError, match="^OpenAI returned HTTP 429"):
        OpenAIEvaluator("synthetic-key")(message("Synthetic message"))
