"""Backfill three months, then resume from the last successful publication."""
from calendar import monthrange
from contextlib import closing
from datetime import datetime, timedelta, timezone
import fcntl
from pathlib import Path
import sqlite3

from .pipeline import sync
from .sheets_client import publish_applications
from .store import list_applications

OVERLAP = timedelta(days=2)
# Gmail searches subjects and bodies. Fetch only candidates for the supported rules.
CANDIDATE_SEARCH = (
    '{application applying apply interview bewerbung vorstellungsgespräch bewerbungsgespräch '
    '"offer you" "additional information" "requested documents" "move forward" "moving forward" '
    '"regret to inform" "job title" stellenbezeichnung}'
)


def _three_months_before(instant):
    year, month = divmod(instant.year * 12 + instant.month - 4, 12)
    month += 1
    return instant.replace(year=year, month=month, day=min(instant.day, monthrange(year, month)[1]))


def _last_success(database, spreadsheet_id):
    if not database.exists():
        return None
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'publication'"
        ).fetchone():
            return None
        row = connection.execute(
            "SELECT completed_through FROM publication WHERE spreadsheet_id = ?", (spreadsheet_id,)
        ).fetchone()
        return datetime.fromtimestamp(row[0], tz=timezone.utc) if row else None


def _set_success(database, spreadsheet_id, cutoff):
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS publication (spreadsheet_id TEXT PRIMARY KEY, completed_through INTEGER NOT NULL)"
        )
        connection.execute(
            """INSERT INTO publication VALUES (?, ?)
               ON CONFLICT(spreadsheet_id) DO UPDATE SET completed_through = excluded.completed_through""",
            (spreadsheet_id, int(cutoff.timestamp())),
        )


def run_agent(messages, sheets, spreadsheet_id: str, database: Path, *, now=None, evaluator=None):
    lock_path = database.with_suffix(database.suffix + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = now or datetime.now(timezone.utc)
        checkpoint = _last_success(database, spreadsheet_id)
        since = checkpoint - OVERLAP if checkpoint else _three_months_before(now)
        query = f"-in:drafts after:{int(since.timestamp())} before:{int(now.timestamp())}"
        if evaluator is None:
            query += f" -in:sent {CANDIDATE_SEARCH}"
        counts = sync(messages, query, None, database, evaluator=evaluator)
        published = publish_applications(
            sheets, spreadsheet_id, list_applications(database), include_notes=evaluator is not None,
        )
        _set_success(database, spreadsheet_id, now)
    return {**counts, "sheet_rows": published, "backfill": checkpoint is None}
