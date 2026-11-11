"""Read-only Gmail operations."""
from googleapiclient.discovery import build

GMAIL_RETRIES = 8


def gmail_messages(credentials):
    return build("gmail", "v1", credentials=credentials, cache_discovery=False).users().messages()


def list_message_ids(credentials, query: str, max_results: int = 100):
    yield from _message_ids(gmail_messages(credentials), query, max_results)


def _message_ids(messages, query, limit):
    page_token = None
    seen = set()
    page_tokens = set()
    while limit is None or len(seen) < limit:
        page = messages.list(
            userId="me", q=query, maxResults=100 if limit is None else min(100, limit - len(seen)),
            pageToken=page_token, fields="messages(id),nextPageToken",
        ).execute(num_retries=GMAIL_RETRIES)
        for item in page.get("messages", []):
            message_id = item["id"]
            if message_id not in seen:
                seen.add(message_id)
                yield message_id
                if len(seen) == limit:
                    return
        next_token = page.get("nextPageToken")
        if not next_token:
            return
        if next_token in page_tokens:
            raise ValueError("Gmail returned a repeated page token")
        page_tokens.add(next_token)
        page_token = next_token


def read_messages(messages, query, limit, *, include_time=False, include_direction=False):
    fields = ["payload"]
    if include_time:
        fields.append("internalDate")
    if include_direction:
        fields.append("labelIds")
    for message_id in _message_ids(messages, query, limit):
        yield messages.get(
            userId="me", id=message_id, format="full",
            fields=",".join(fields),
        ).execute(num_retries=GMAIL_RETRIES)
