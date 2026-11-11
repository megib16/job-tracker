import json
import sqlite3
from unittest.mock import Mock

import pytest

from job_tracker.main import main, sync
from job_tracker.models import Application, Status
from job_tracker.store import list_applications, save_updates

from test_ingestion import FIELDS, message


def record(status="applied", **overrides):
    return Application(**{"company": "Example", "position": "Analyst", "status": status, **overrides})


def test_repeat_sync_updates_one_row_and_older_mail_cannot_revert(tmp_path):
    db = tmp_path / "applications.sqlite"
    applied = record(application_date="2026-10-01")
    rejected = record("rejected", job_id="ABC-12", rejection_date="2026-10-15")
    assert save_updates(db, [(10, applied)])["created"] == 1
    assert save_updates(db, [(20, rejected)])["updated"] == 1
    assert save_updates(db, [(20, rejected), (10, applied)])["unchanged"] == 2
    assert list_applications(db) == [record(
        "rejected", job_id="ABC-12", application_date="2026-10-01", rejection_date="2026-10-15",
    )]
    assert db.stat().st_mode & 0o777 == 0o600


def test_different_job_ids_stay_separate_and_role_only_update_is_ambiguous(tmp_path):
    db = tmp_path / "applications.sqlite"
    updates = [(1, record(job_id="A")), (2, record(job_id="B")), (0, record("rejected"))]
    counts = save_updates(db, updates)
    assert counts == {"created": 2, "updated": 0, "unchanged": 0, "needs_review": 1}
    assert {row.job_id for row in list_applications(db)} == {"A", "B"}
    assert all(row.status == Status.APPLIED for row in list_applications(db))


def test_exact_id_takes_precedence_when_role_name_changes(tmp_path):
    db = tmp_path / "applications.sqlite"
    save_updates(db, [(1, record(job_id="ABC")), (2, record(position="Senior Analyst"))])
    save_updates(db, [(3, record("interview", job_id="abc", position="Senior Analyst"))])
    rows = list_applications(db)
    assert len(rows) == 2
    assert {row.job_id: row.status for row in rows} == {None: Status.APPLIED, "abc": Status.INTERVIEW}


def test_missing_timestamp_is_reviewed_and_failed_scan_does_not_write(tmp_path):
    messages = Mock()
    messages.list.return_value.execute.return_value = {"messages": [{"id": "first"}, {"id": "second"}]}
    valid = {**message(FIELDS, "Application received"), "internalDate": "100"}
    missing_date = message(FIELDS, "Application received")
    db = tmp_path / "applications.sqlite"
    messages.get.return_value.execute.side_effect = [valid, OSError("network failure")]
    with pytest.raises(OSError):
        sync(messages, "label:test", 2, db)
    assert not db.exists()
    messages.get.return_value.execute.side_effect = [valid, missing_date]
    assert sync(messages, "label:test", 2, db) == {
        "scanned": 2, "parse_needs_review": 1, "no_signal": 0,
        "created": 1, "updated": 0, "unchanged": 0, "needs_review": 0,
    }
    assert messages.get.call_args.kwargs["fields"] == "payload,internalDate"


def test_show_works_offline_and_only_exposes_application_fields(tmp_path, capsys):
    db = tmp_path / "applications.sqlite"
    application = record()
    save_updates(db, [(100, application)])
    assert main(["--show", "--db", str(db)]) == 0
    expected = application.model_dump(mode="json")
    expected["application_date"] = "1970-01-01"
    assert json.loads(capsys.readouterr().out) == [expected]

def test_receipt_dates_fill_only_the_matching_event_and_survive_replay(tmp_path):
    db = tmp_path / "applications.sqlite"
    day = 86_400_000
    applied = record()
    rejected = record("rejected")
    save_updates(db, [(3 * day, rejected)])
    assert list_applications(db) == [record("rejected", rejection_date="1970-01-04")]
    assert save_updates(db, [(day, applied)])["updated"] == 1
    assert save_updates(db, [(2 * day, applied), (3 * day, rejected)])["unchanged"] == 2
    assert list_applications(db) == [
        record("rejected", application_date="1970-01-02", rejection_date="1970-01-04"),
    ]
    explicit = record(application_date="1970-01-01")
    save_updates(db, [(day, explicit)])
    assert list_applications(db)[0].application_date == explicit.application_date


def test_existing_database_gets_receipt_dates_without_recreating_rows(tmp_path):
    db = tmp_path / "applications.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE applications (id INTEGER PRIMARY KEY, company_key TEXT, position_key TEXT, "
            "job_key TEXT, record TEXT, status_at INTEGER)"
        )
        connection.execute(
            "INSERT INTO applications VALUES (42, 'example', 'analyst', '', ?, 100)",
            (record().model_dump_json(),),
        )
    db.chmod(0o600)
    assert list_applications(db) == [record(application_date="1970-01-01")]
    assert save_updates(db, [(86_400_000, record("interview"))])["updated"] == 1
    assert list_applications(db) == [record("interview", application_date="1970-01-01")]
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT id FROM applications").fetchall() == [(42,)]
