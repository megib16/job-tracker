"""Decode inline text and classify explicit evidence without external inference."""
import base64
import binascii
import re
from email.header import decode_header, make_header
from email.message import Message
from html.parser import HTMLParser

from pydantic import ValidationError

from .models import Application, Status

MAX_TEXT = 64_000
STATUSES = {
    Status.APPLIED: r"application (?:received|confirmation)|thank you for applying|we (?:have )?received your application|vielen dank für (?:ihre|deine) bewerbung|(?:ihre|deine) bewerbung ist bei uns eingegangen",
    Status.UNDER_REVIEW: r"your application is (?:under review|being reviewed)|wir prüfen (?:ihre|deine) bewerbung",
    Status.ADDITIONAL_INFORMATION: r"please (?:provide|send|submit) (?:additional information|the requested documents)",
    Status.INTERVIEW: r"interview invitation|invite you (?:to|for) an interview|einladung zum (?:vorstellungs|bewerbungs)gespräch",
    Status.REJECTED: r"we (?:will not|won.t) (?:be moving|move) forward|we regret to inform you|your application was unsuccessful|we regret that we cannot progress your application|(?:ihre|deine) bewerbung (?:leider )?nicht (?:weiter )?berücksichtigen",
    Status.OFFER: r"we (?:are pleased to |would like to )offer you the (?:position|role)",
    Status.WITHDRAWN: r"your application (?:has been|was) withdrawn",
}
FIELD = re.compile(
    r"^(company|employer|position|job title|role|job id|requisition id|application date|rejection date"
    r"|unternehmen|arbeitgeber|stellenbezeichnung|stellen-id|referenznummer|bewerbungsdatum|absagedatum)"
    r"[ \t]*:[ \t]*([^\r\n]+)$", re.IGNORECASE | re.MULTILINE,
)
REJECTION_POSITION = re.compile(
    r'application for the position[ \t]+["“]([^"”\r\n]+)["”][ \t]*[-–—][ \t]*([A-Z0-9][A-Z0-9-]*)\.',
    re.IGNORECASE,
)
REJECTION_COMPANY = re.compile(
    r"^Thank you for your interest and application to (?:the )?([^\r\n]+?)\.[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
CONFIRMATION_COMPANY = re.compile(
    r"^Thank you for your interest in joining ([^!\r\n]+)!\s*$",
    re.IGNORECASE | re.MULTILINE,
)
CONFIRMATION_POSITION = re.compile(
    r"we appreciate your time and effort to apply for the position of\s+"
    r"([^\r\n]+?)\s+and we are glad that you want to hop on board with us\b",
    re.IGNORECASE,
)
APPLIED_FOR_AT = re.compile(
    r"^thank you for applying for the (?:position of ([^\r\n]+?)|([^\r\n]+?) position) at ([^\r\n]+?)"
    r"[.!]\s*$", re.IGNORECASE | re.MULTILINE,
)
FIELD_NAMES = {
    "employer": "company", "job title": "position", "role": "position", "requisition id": "job_id",
    "unternehmen": "company", "arbeitgeber": "company", "stellenbezeichnung": "position",
    "stellen-id": "job_id", "referenznummer": "job_id",
    "bewerbungsdatum": "application_date", "absagedatum": "rejection_date",
}


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head", "blockquote"}:
            self.hidden.append(tag)
        if not self.hidden and tag in {"br", "div", "p", "li", "tr"}:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if not self.hidden and tag in {"div", "p", "li", "tr"}:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)


def _headers(part):
    headers = Message()
    for header in part.get("headers", []):
        headers[header["name"]] = header["value"]
    return headers


def _inline_text(part):
    headers = _headers(part)
    if part.get("filename") or headers.get_content_disposition() == "attachment":
        return None
    mime = part.get("mimeType", "").lower()
    if mime.startswith("multipart/"):
        children = [item for child in part.get("parts", []) if (item := _inline_text(child))]
        if mime == "multipart/alternative":
            return next((item for item in children if item[0] == "text/plain"), children[0] if children else None)
        if children:
            return children[0][0], "\n".join(body for _, body in children)[:MAX_TEXT]
        return None
    data = part.get("body", {}).get("data")
    if mime not in {"text/plain", "text/html"} or not data:
        return None
    data = data[:MAX_TEXT * 4]
    raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
    body = raw.decode(headers.get_content_charset() or "utf-8", errors="replace")[:MAX_TEXT]
    if mime == "text/html":
        parser = _VisibleText()
        parser.feed(body)
        parser.close()
        body = "".join(parser.chunks)
    return mime, body


def decode_message(message: dict) -> tuple[str, str]:
    payload = message["payload"]
    subject = str(make_header(decode_header(_headers(payload).get("Subject", ""))))
    inline = _inline_text(payload)
    body = inline[1] if inline else ""
    body = re.split(r"^\s*(?:>.*|On .+ wrote:|[- ]*Forwarded message[- ]*|[- ]*Original Message[- ]*)$",
                    body, maxsplit=1, flags=re.IGNORECASE | re.MULTILINE)[0]
    return subject, body


def classify_message(message: dict) -> tuple[str, Application | None]:
    """Return a validated candidate or a review outcome without source text."""
    try:
        subject, body = decode_message(message)
    except (KeyError, TypeError, ValueError, LookupError, binascii.Error):
        return "needs_review", None

    # A reply subject can describe an earlier stage.
    if re.match(r"^(?:re|fw|fwd):", subject, flags=re.IGNORECASE):
        subject = ""
    statuses = {status for status, pattern in STATUSES.items()
                if re.search(rf"\b(?:{pattern})\b", subject + "\n" + body, re.IGNORECASE)}
    confirmation_companies = CONFIRMATION_COMPANY.findall(body)
    confirmation_positions = CONFIRMATION_POSITION.findall(body)
    if confirmation_companies and confirmation_positions:
        statuses.add(Status.APPLIED)
    if not statuses:
        return "needs_review" if FIELD.search(body) else "no_signal", None
    if len(statuses) != 1:
        return "needs_review", None

    status = statuses.pop()
    candidates = [(FIELD_NAMES.get(key.lower(), key.lower().replace(" ", "_")), value)
                  for key, value in FIELD.findall(body)]
    if status == Status.APPLIED and confirmation_companies and confirmation_positions:
        candidates.extend(("company", company) for company in confirmation_companies)
        candidates.extend(("position", position) for position in confirmation_positions)
    if status == Status.APPLIED:
        for position, alternate_position, company in APPLIED_FOR_AT.findall(body):
            candidates.extend((("company", company), ("position", position or alternate_position)))
    if status == Status.REJECTED:
        for position, job_id in REJECTION_POSITION.findall(body):
            candidates.extend((("position", position), ("job_id", job_id)))
        candidates.extend(("company", company) for company in REJECTION_COMPANY.findall(body))

    fields = {}
    for key, value in candidates:
        value = value.strip()
        if key in fields and fields[key] != value:
            return "needs_review", None
        if not value or any(ord(char) < 32 or not char.isprintable() for char in value):
            return "needs_review", None
        if "@" in value or re.search(r"(?:https?://|www\.)", value, re.IGNORECASE):
            return "needs_review", None
        if key.endswith("_date") and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return "needs_review", None
        fields[key] = value
    try:
        return "classified", Application(status=status, **fields)
    except ValidationError:
        return "needs_review", None
