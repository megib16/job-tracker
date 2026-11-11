import base64
import json
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from job_tracker.config import GMAIL_SCOPES
from job_tracker.gmail_auth import AUTH_URI, TOKEN_URI, authorize, load_credentials
from job_tracker.ingestion import classify_message
from job_tracker.main import dry_run, main

FIELDS = "Company: Example GmbH\nPosition: Data Analyst\nJob ID: ABC-12"


def part(text, mime="text/plain", **extra):
    return {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")}, **extra}


def message(body, subject=""):
    return {"payload": part(body, headers=[{"name": "Subject", "value": subject}])}


def test_classifies_explicit_fields_and_dates():
    outcome, record = classify_message(message(FIELDS + "\nApplication Date: 2026-10-01", "Application received"))
    assert outcome == "classified"
    assert record.model_dump(mode="json") == {
        "company": "Example GmbH", "position": "Data Analyst", "job_id": "ABC-12",
        "status": "applied", "application_date": "2026-10-01", "rejection_date": None,
    }


def test_prefers_plain_text_and_skips_attachments_and_quotes():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                part("We regret to inform you", "text/html"),
                part("We invite you to an interview\n" + FIELDS + "\n> Application received"),
            ]},
            part("We regret to inform you", filename="notes.txt"),
            part("We regret to inform you", headers=[{"name": "Content-Disposition", "value": "attachment"}]),
        ],
    }
    payload["headers"] = [{"name": "Subject", "value": "Re: Application received"}]
    outcome, record = classify_message({"payload": payload})
    assert outcome == "classified"
    assert record.status.value == "interview"


def test_html_only_and_encoded_subject():
    payload = part("<p>Company: Example GmbH</p><p>Position: Data Analyst</p>"
                   "<script>We regret to inform you</script>", "text/html",
                   headers=[{"name": "Subject", "value": "=?utf-8?Q?Application_received?="}])
    assert classify_message({"payload": payload})[0] == "classified"


@pytest.mark.parametrize(("body", "subject", "outcome"), [
    ("Personal hello from Ada", "", "no_signal"),
    ("Thank you for applying", "", "needs_review"),
    (FIELDS + "\nCompany: Other", "Application received", "needs_review"),
    (FIELDS + "\nWe invite you to an interview", "Application received", "needs_review"),
    (FIELDS + "\nApplication Date: 2026-02-31", "Application received", "needs_review"),
    (FIELDS.replace("Data Analyst", "ada@example.com"), "Application received", "needs_review"),
    (FIELDS + "\nRejection Date: 2026-10-01", "Application received", "needs_review"),
])
def test_ambiguous_or_invalid_messages(body, subject, outcome):
    assert classify_message(message(body, subject)) == (outcome, None)


def test_malformed_inline_text_needs_review():
    assert classify_message({"payload": {"mimeType": "text/plain", "body": {"data": "!"}}}) == ("needs_review", None)


def test_dry_run_pages_and_outputs_counts_only():
    messages = Mock()
    messages.list.return_value.execute.side_effect = [
        {"messages": [{"id": "private-1"}], "nextPageToken": "second"},
        {"messages": [{"id": "private-1"}, {"id": "private-2"}], "nextPageToken": "third"},
    ]
    messages.get.return_value.execute.side_effect = [
        message(FIELDS, "Application received"),
        message("Recruiter: private@example.com"),
    ]
    assert dry_run(messages, "label:jobs", 2) == {
        "scanned": 2, "classified": 1, "needs_review": 0, "no_signal": 1, "by_status": {"applied": 1},
    }
    assert [call.kwargs["pageToken"] for call in messages.list.call_args_list] == [None, "second"]
    assert [call.kwargs["id"] for call in messages.get.call_args_list] == ["private-1", "private-2"]
    assert {call[0].split("(")[0] for call in messages.mock_calls} == {"list", "get"}


def test_cli_sanitizes_gmail_api_errors(monkeypatch, capsys):
    monkeypatch.setattr("job_tracker.main.load_credentials", lambda _: None)
    messages = Mock()
    messages.list.return_value.execute.side_effect = HttpError(
        Response({"status": "403"}), b"private@example.com", uri="https://example.com/private",
    )
    monkeypatch.setattr("job_tracker.main.gmail_messages", lambda _: messages)
    assert main(["--dry-run"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Gmail operation failed" in captured.err
    assert "private" not in captured.err


def test_token_file_rejects_broad_scope_and_public_mode(tmp_path):
    token = tmp_path / "token.json"
    info = {"scopes": GMAIL_SCOPES, "token_uri": TOKEN_URI, "token": "cached",
            "refresh_token": "synthetic", "client_id": "synthetic", "client_secret": "synthetic"}
    token.write_text(json.dumps(info))
    token.chmod(0o644)
    with pytest.raises(ValueError, match="private token file"):
        load_credentials(token)
    token.chmod(0o600)
    assert load_credentials(token).token is None
    info["scopes"] = [*GMAIL_SCOPES, "https://mail.google.com/"]
    token.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="read-only token"):
        load_credentials(token)


def test_authorize_creates_private_refresh_only_file(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"installed": {"token_uri": TOKEN_URI, "auth_uri": AUTH_URI,
                                            "client_id": "synthetic", "client_secret": "synthetic"}}))
    token = tmp_path / "token.json"
    flow = Mock()
    credentials = flow.run_local_server.return_value
    credentials.granted_scopes = GMAIL_SCOPES
    credentials.refresh_token = "synthetic"
    credentials.to_json.return_value = json.dumps({
        "scopes": GMAIL_SCOPES, "token_uri": TOKEN_URI, "refresh_token": "synthetic", "token": "cached",
        "client_id": "synthetic", "client_secret": "synthetic", "account": "private@example.com",
    })
    factory = Mock(return_value=flow)
    monkeypatch.setattr("job_tracker.gmail_auth.InstalledAppFlow.from_client_config", factory)
    authorize(client, token)
    assert factory.call_args.args[1] == GMAIL_SCOPES
    assert token.stat().st_mode & 0o777 == 0o600
    assert set(json.loads(token.read_text())) == {"client_id", "client_secret", "refresh_token", "scopes", "token_uri"}
    with pytest.raises(FileExistsError):
        authorize(client, token)


def test_authorize_reports_missing_client_without_exposing_path(tmp_path, capsys):
    assert main(["--authorize", str(tmp_path / "private-client.json")]) == 1
    captured = capsys.readouterr()
    assert "OAuth client JSON was not found" in captured.err
    assert "private-client.json" not in captured.err


def test_rejection_extracts_role_id_and_company_from_prose():
    body = (
        'Hi Applicant,\nMany thanks for your application for the position '
        '"Werkstudent Datenanalyse" - 123456.\n'
        'We regret that we cannot progress your application on this occasion.\n'
        'Thank you for your interest and application to the Example Group.'
    )
    outcome, record = classify_message(message(body, "Your application"))
    assert outcome == "classified"
    assert record.model_dump(mode="json") == {
        "company": "Example Group", "position": "Werkstudent Datenanalyse", "job_id": "123456",
        "status": "rejected", "application_date": None, "rejection_date": None,
    }
    conflicting = body + '\nCompany: Different Group'
    assert classify_message(message(conflicting, "Your application")) == ("needs_review", None)


def test_confirmation_extracts_company_and_role_from_paired_phrases():
    company_line = "Thank you for your interest in joining Example Group!\u00a0"
    role_line = (
        "We appreciate your time and effort to apply for the position of Data Analyst "
        "and we are glad that you want to hop on board with us."
    )
    outcome, record = classify_message(message(f"Hi Applicant,\n{company_line}\n{role_line}"))
    assert outcome == "classified"
    assert record.model_dump(mode="json") == {
        "company": "Example Group", "position": "Data Analyst", "job_id": None,
        "status": "applied", "application_date": None, "rejection_date": None,
    }
    assert classify_message(message(company_line)) == ("no_signal", None)
    assert classify_message(message(role_line)) == ("no_signal", None)
    assert classify_message(message(f"{company_line}\n{role_line}", "Interview invitation")) == (
        "needs_review", None,
    )
