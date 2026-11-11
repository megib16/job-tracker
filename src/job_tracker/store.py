"""Store validated application fields and the time of the latest status update."""
from collections import Counter
from contextlib import closing
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sqlite3
from stat import S_ISREG

from pydantic import ValidationError

from .models import Application, ApplicationWithNotes, Status


def _load_record(raw: str) -> Application:
    data = json.loads(raw)
    model = ApplicationWithNotes if "notes" in data else Application
    return model.model_validate(data)


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


def _arrival_day(timestamp: int) -> date:
    return date(1970, 1, 1) + timedelta(days=timestamp // 86_400_000)


def _with_receipt_dates(record: Application, status_at: int, applied_on: str | None) -> Application:
    application_date = record.application_date or (date.fromisoformat(applied_on) if applied_on else None)
    rejection_date = record.rejection_date
    if record.status == Status.REJECTED and rejection_date is None:
        rejection_date = _arrival_day(status_at)
    if application_date and rejection_date and rejection_date < application_date:
        # A receipt date is only an estimate. Keep the explicit date if they disagree.
        application_date = record.application_date
        rejection_date = record.rejection_date
    return record.model_copy(update={"application_date": application_date, "rejection_date": rejection_date})


def _has_applied_on(connection) -> bool:
    return any(column[1] == "applied_on" for column in connection.execute("PRAGMA table_info(applications)"))


def _save(connection, incoming: Application, received_at: int) -> str:
    company = _key(incoming.company)
    position = _key(incoming.position)
    job = _key(incoming.job_id or "")
    candidates = connection.execute(
        """SELECT id, job_key, position_key, record, status_at, applied_on FROM applications
           WHERE company_key = ? AND (position_key = ? OR job_key = ?)""",
        (company, position, job),
    ).fetchall()

    # Prefer an exact job ID. A role match is eligible when either ID is missing.
    matches = [row for row in candidates if job and row[1] == job]
    if not matches:
        matches = [row for row in candidates if row[2] == position and not (job and row[1])]
    if len(matches) > 1:
        return "needs_review"
    incoming_applied_on = _arrival_day(received_at).isoformat() if incoming.status == Status.APPLIED else None
    if not matches:
        connection.execute(
            """INSERT INTO applications (company_key, position_key, job_key, record, status_at, applied_on)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (company, position, job, incoming.model_dump_json(), received_at, incoming_applied_on),
        )
        return "created"

    row_id, _, _, stored, status_at, applied_on = matches[0]
    current = _load_record(stored)
    if received_at == status_at and incoming.status != current.status:
        return "needs_review"
    if current.application_date and incoming.application_date and current.application_date != incoming.application_date:
        return "needs_review"

    latest, earlier = (incoming, current) if received_at > status_at else (current, incoming)
    rejection_date = latest.rejection_date
    if latest.status == earlier.status == Status.REJECTED:
        rejection_date = rejection_date or earlier.rejection_date
    try:
        merged = type(latest)(
            company=latest.company, position=latest.position,
            job_id=latest.job_id or earlier.job_id, status=latest.status,
            application_date=latest.application_date or earlier.application_date,
            rejection_date=rejection_date,
            **({"notes": latest.notes} if isinstance(latest, ApplicationWithNotes) else {}),
        )
    except ValidationError:
        return "needs_review"

    new_status_at = max(received_at, status_at)
    new_applied_on = applied_on
    if incoming_applied_on and (applied_on is None or incoming_applied_on < applied_on):
        new_applied_on = incoming_applied_on
    if merged == current and new_status_at == status_at and new_applied_on == applied_on:
        return "unchanged"
    connection.execute(
        """UPDATE applications SET position_key = ?, job_key = ?, record = ?, status_at = ?, applied_on = ?
           WHERE id = ?""",
        (_key(merged.position), _key(merged.job_id or ""), merged.model_dump_json(), new_status_at, new_applied_on, row_id),
    )
    before = _with_receipt_dates(current, status_at, applied_on)
    after = _with_receipt_dates(merged, new_status_at, new_applied_on)
    return "updated" if before != after else "unchanged"


def _check_file(path: Path):
    mode = path.lstat().st_mode
    if not S_ISREG(mode) or mode & 0o077:
        raise ValueError("Use a private tracker file with mode 0600.")


def save_updates(path: Path, updates: list[tuple[int, Application]]) -> dict:
    counts = Counter({"created": 0, "updated": 0, "unchanged": 0, "needs_review": 0})
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _check_file(path)
    else:
        os.close(fd)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS applications (
               id INTEGER PRIMARY KEY, company_key TEXT NOT NULL, position_key TEXT NOT NULL,
               job_key TEXT NOT NULL, record TEXT NOT NULL, status_at INTEGER NOT NULL, applied_on TEXT)"""
        )
        if not _has_applied_on(connection):
            connection.execute("ALTER TABLE applications ADD COLUMN applied_on TEXT")
            existing = connection.execute("SELECT id, record, status_at FROM applications").fetchall()
            for row_id, raw, status_at in existing:
                if _load_record(raw).status == Status.APPLIED:
                    connection.execute(
                        "UPDATE applications SET applied_on = ? WHERE id = ?",
                        (_arrival_day(status_at).isoformat(), row_id),
                    )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS applications_company ON applications (company_key)"
        )
        # Establish known IDs before matching role-only messages in the same batch.
        ordered = sorted(updates, key=lambda item: (not item[1].job_id, item[0]))
        for received_at, application in ordered:
            counts[_save(connection, application, received_at)] += 1
    return dict(counts)


def list_applications(path: Path) -> list[Application]:
    _check_file(path)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        has_applied_on = _has_applied_on(connection)
        query = (
            "SELECT record, status_at, applied_on FROM applications" if has_applied_on else
            "SELECT record, status_at, NULL FROM applications"
        )
        results = []
        for raw, status_at, applied_on in connection.execute(query + " ORDER BY company_key, position_key, job_key"):
            record = _load_record(raw)
            if not has_applied_on and record.status == Status.APPLIED:
                applied_on = _arrival_day(status_at).isoformat()
            results.append(_with_receipt_dates(record, status_at, applied_on))
        return results
