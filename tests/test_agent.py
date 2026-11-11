from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from job_tracker.agent import run_agent
from job_tracker.ingestion import classify_message
from job_tracker.sheets_client import create_tracker

from test_ingestion import message


def test_agent_backfills_all_pages_retries_publication_and_resumes(tmp_path):
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    next_run = datetime(2026, 6, 1, tzinfo=timezone.utc)
    database = tmp_path / "agent.sqlite"
    gmail = Mock()
    gmail.list.return_value.execute.side_effect = [
        {"messages": [{"id": "first"}], "nextPageToken": "next"},
        {"messages": [{"id": "second"}]},
        {"messages": [{"id": "first"}, {"id": "second"}]},
        {"messages": [{"id": "third"}]},
    ]
    received = "1779004800000"
    company_role = "Company: Example GmbH\nPosition: =IMPORTDATA(placeholder)\nJob ID: ABC-12"
    applied = {**message(company_role, "Application received"), "internalDate": received}
    ignored = {**message("Unrelated email"), "internalDate": received}
    rejected = {**message(company_role, "Your application was unsuccessful"), "internalDate": "1780272000000"}
    gmail.get.return_value.execute.side_effect = [applied, ignored, applied, ignored, rejected]

    sheets = Mock()
    sheets.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Applications", "sheetId": 5, "gridProperties": {"rowCount": 1}}}]
    }
    sheets.batchUpdate.return_value.execute.side_effect = [OSError("temporary"), {}, {}]
    with pytest.raises(OSError):
        run_agent(gmail, sheets, "sheet", database, now=now)
    retried = run_agent(gmail, sheets, "sheet", database, now=now)
    assert retried["backfill"] and retried["sheet_rows"] == 1
    assert retried["unchanged"] == 1

    following = run_agent(gmail, sheets, "sheet", database, now=next_run)
    assert not following["backfill"] and following["sheet_rows"] == 1
    assert following["updated"] == 1

    calls = gmail.list.call_args_list
    initial_query = calls[0].kwargs["q"]
    assert f"after:{int(datetime(2026, 2, 28, tzinfo=timezone.utc).timestamp())}" in initial_query
    assert calls[1].kwargs["pageToken"] == "next"
    assert calls[2].kwargs["q"] == initial_query
    assert f"after:{int(datetime(2026, 5, 29, tzinfo=timezone.utc).timestamp())}" in calls[3].kwargs["q"]

    requests = sheets.batchUpdate.call_args.kwargs["body"]["requests"]
    assert requests[0]["updateSheetProperties"]["properties"]["gridProperties"]["rowCount"] == 2
    update = requests[1]["updateCells"]
    cells = [item["userEnteredValue"] for item in update["rows"][1]["values"]]
    assert cells == [
        {"stringValue": "Example GmbH"}, {"stringValue": "=IMPORTDATA(placeholder)"},
        {"stringValue": "ABC-12"}, {"stringValue": "rejected"},
        {"stringValue": "2026-05-17"}, {"stringValue": "2026-06-01"},
    ]


def test_create_sheet_uses_dedicated_tab_and_returns_link():
    sheets = Mock()
    sheets.create.return_value.execute.return_value = {
        "spreadsheetId": "sheet", "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/sheet/edit",
    }
    assert create_tracker(sheets)["spreadsheetId"] == "sheet"
    properties = sheets.create.call_args.kwargs["body"]["sheets"][0]["properties"]
    assert properties == {"title": "Applications", "gridProperties": {"frozenRowCount": 1}}


@pytest.mark.parametrize("body", [
    "Thank you for applying for the Data Analyst position at Example GmbH.",
    "Vielen Dank für Ihre Bewerbung!\nUnternehmen: Example GmbH\nStellenbezeichnung: Data Analyst",
])
def test_general_english_and_german_confirmation(body):
    outcome, record = classify_message(message(body))
    assert outcome == "classified"
    assert (record.company, record.position, record.status.value) == ("Example GmbH", "Data Analyst", "applied")
