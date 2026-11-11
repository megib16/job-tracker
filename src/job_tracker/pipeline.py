"""Scan Gmail and commit validated application updates after a successful read."""
from collections import Counter

from .gmail_client import read_messages
from .ingestion import classify_message
from .store import save_updates

MAX_GMAIL_TIMESTAMP = 253_402_300_799_999  # Last millisecond representable by a Python date.


def dry_run(messages, query, limit, *, evaluator=None):
    counts = Counter({"classified": 0, "needs_review": 0, "no_signal": 0})
    statuses = Counter()
    classify = evaluator if evaluator is not None else classify_message
    for message in read_messages(
        messages, query, limit, include_time=evaluator is not None, include_direction=evaluator is not None,
    ):
        outcome, record = classify(message)
        counts[outcome] += 1
        if record is not None:
            statuses[record.status.value] += 1
    return {"scanned": counts.total(), **counts, "by_status": dict(statuses)}


def sync(messages, query, limit, database, *, evaluator=None):
    updates = []
    counts = Counter({"scanned": 0, "parse_needs_review": 0, "no_signal": 0})
    classify = evaluator if evaluator is not None else classify_message
    for message in read_messages(messages, query, limit, include_time=True, include_direction=evaluator is not None):
        counts["scanned"] += 1
        outcome, record = classify(message)
        if record is None:
            counts["parse_needs_review" if outcome == "needs_review" else outcome] += 1
            continue
        timestamp = message.get("internalDate")
        if (
            not isinstance(timestamp, str) or not timestamp.isascii() or not timestamp.isdecimal()
            or len(timestamp) > 15 or int(timestamp) > MAX_GMAIL_TIMESTAMP
        ):
            counts["parse_needs_review"] += 1
            continue
        updates.append((int(timestamp), record))
    return {**counts, **save_updates(database, updates)}
