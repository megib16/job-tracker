"""Publish validated fields into the tracker-owned worksheet."""
from googleapiclient.discovery import build

HEADERS = ("Company", "Position", "Job ID", "Status", "Application Date", "Rejection Date")
SHEET_TITLE = "Applications"
AI_SHEET_TITLE = "AI Applications"
FILE_TITLE = "Job Application Tracker"


def spreadsheets(credentials):
    return build("sheets", "v4", credentials=credentials, cache_discovery=False).spreadsheets()


def create_tracker(sheets):
    # Creation is intentionally not retried on an uncertain response.
    return sheets.create(
        body={
            "properties": {"title": FILE_TITLE},
            "sheets": [{"properties": {"title": SHEET_TITLE, "gridProperties": {"frozenRowCount": 1}}}],
        },
        fields="spreadsheetId,spreadsheetUrl",
    ).execute()


def publish_applications(sheets, spreadsheet_id, applications, *, include_notes=False):
    spreadsheet = sheets.get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title,gridProperties(rowCount)))",
    ).execute(num_retries=2)
    title = AI_SHEET_TITLE if include_notes else SHEET_TITLE
    tabs = [tab["properties"] for tab in spreadsheet["sheets"]]
    target = next((tab for tab in tabs if tab["title"] == title), None)
    if target is None and not include_notes:
        raise ValueError("The spreadsheet needs an Applications tab created by the tracker.")

    headers = (*HEADERS, "Notes") if include_notes else HEADERS
    rows = [headers]
    rows.extend((
        row.company, row.position, row.job_id or "", row.status.value,
        row.application_date.isoformat() if row.application_date else "",
        row.rejection_date.isoformat() if row.rejection_date else "",
    ) + ((getattr(row, "notes", ""),) if include_notes else ()) for row in applications)
    requests = []
    if target is None:
        sheet_id = max((tab["sheetId"] for tab in tabs), default=0) + 1
        row_count = max(1000, len(rows))
        requests.append({"addSheet": {"properties": {
            "sheetId": sheet_id, "title": title,
            "gridProperties": {"rowCount": row_count, "columnCount": 26, "frozenRowCount": 1},
        }}})
    else:
        sheet_id = target["sheetId"]
        row_count = max(len(rows), target["gridProperties"]["rowCount"])
    if target is not None and row_count > target["gridProperties"]["rowCount"]:
        requests.append({"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"rowCount": row_count}},
            "fields": "gridProperties.rowCount",
        }})
    # Explicit string cells prevent email-derived values from becoming formulas.
    requests.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": row_count,
                  "startColumnIndex": 0, "endColumnIndex": len(headers)},
        "rows": [{"values": [{"userEnteredValue": {"stringValue": cell}} for cell in row]} for row in rows],
        "fields": "userEnteredValue",
    }})
    # After an uncertain create response, the next run can discover the tab.
    sheets.batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute(
        num_retries=0 if target is None else 2,
    )
    return len(rows) - 1
