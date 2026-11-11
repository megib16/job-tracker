# Job Application Agent

A Gmail read-only tracker for job application updates. It processes matching messages in memory and can save validated application records to a private SQLite database. The dry run prints aggregate counts. Gmail access is read-only.

## Gmail to Google Sheets

The first agent run searches incoming mail from the previous three calendar months. Gmail finds candidate messages using English and German application terms, and the tracker fetches and parses those candidates. Later runs pick up where the last successful publication stopped, with two days of overlap for late mail. The agent updates one dedicated **Applications** tab from the private tracker database. That tab is managed by the agent. Keep personal notes on a separate tab.

The existing Gmail read-only token can be reused. In the OAuth client's Google Cloud project, enable the [Google Sheets API](https://console.cloud.google.com/apis/library/sheets.googleapis.com). Run these commands with the compatible Python environment you used for Gmail setup.

```sh
"$TRACKER_PYTHON" -m job_tracker.main --authorize-sheets "$CLIENT_FILE"
"$TRACKER_PYTHON" -m job_tracker.main --create-sheet
```

The first command requests the [per-file Drive scope](https://developers.google.com/workspace/sheets/api/scopes) and saves a separate private token. The second creates your spreadsheet and prints its link and ID. Save that ID. To run the first backfill and subsequent updates, use the same command:

```sh
"$TRACKER_PYTHON" -m job_tracker.main --run-agent --spreadsheet-id "$SPREADSHEET_ID"
```

The agent database defaults to `~/.config/job-application-agent/agent.sqlite`. Keep it on a persistent private filesystem. The Google Sheet contains the six application fields. Unfamiliar or ambiguous emails can be missed or counted for review, and message contents are never written to the sheet. A failed Sheets update leaves the checkpoint unchanged so the next run can retry.

For unattended updates on a Linux VM, install the package into a compatible Python environment on the VM. Transfer the two private token files separately from the source and keep them in the job user's `~/.config/job-application-agent` directory with mode 0600. Add the following line with `crontab -e` under that same account, using the actual absolute Python path and spreadsheet ID. It runs at minute 15 of every hour.

```cron
15 * * * * /path/to/python -m job_tracker.main --run-agent --spreadsheet-id YOUR_SPREADSHEET_ID
```

Before relying on unattended runs, check the OAuth app's publishing status. External apps in Testing issue Gmail refresh tokens that [expire after seven days](https://developers.google.com/identity/protocols/oauth2#expiration). Do not bake either token into an image or paste it into a command or log.

## AI extraction pilot

The optional OpenAI and Anthropic evaluators use a strict output schema to classify cleaned email text. It redacts recognizable links and contact details and trims common greetings, signatures, and quoted replies. Redaction is best effort. Selected message text goes to the chosen API provider and can contain personal data. OpenAI requests use `store: false`. See [OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data) for OpenAI retention and [Anthropic’s API documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) for Claude structured output details.

Set `OPENAI_API_KEY` in a private environment or secret manager. Set `JOB_TRACKER_OPENAI_MODEL` to override the default model. Start with a small read-only sample that prints counts:

```sh
"$TRACKER_PYTHON" -m job_tracker.main --dry-run --use-openai --query 'label:tracker.test' --limit 10
```

For Claude, set `ANTHROPIC_API_KEY` privately and use `--use-anthropic` in place of `--use-openai`. The default is Claude Haiku 5.5, which you can override with `JOB_TRACKER_ANTHROPIC_MODEL`.

The opt-in full run uses a separate database and publishes to an **AI Applications** tab in the same spreadsheet, leaving the original **Applications** tab alone. It uses date-based Gmail discovery, including sent mail, and submits available cleaned text to the chosen provider for classification. It can incur an API charge per processed message. An earlier sent application or confirmation can supply an application date for a matched rejection. Keep manual notes on a separate tab so they stay intact during sync.

```sh
"$TRACKER_PYTHON" -m job_tracker.main --run-agent --use-openai --spreadsheet-id "$SPREADSHEET_ID"
```

## Local setup

The project requires Python 3.11 or newer. Check the existing environment first.

```sh
.venv/bin/python --version
```

If it meets the requirement, install and test with that interpreter.

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

If your existing environment uses Python 3.9, keep it and use the container below or another existing Python 3.11+ environment. These commands never create a virtual environment.

## Authorize Gmail once

1. In Google Cloud, enable the Gmail API, configure your OAuth consent screen, and create a **Desktop app** OAuth client. Add your Gmail account as a test user if your app is in Testing. Use only the [Gmail read-only scope](https://developers.google.com/workspace/gmail/api/auth/scopes).
2. Download the Desktop OAuth client JSON to a private location outside the project. Set `CLIENT_FILE` to its absolute path and run these commands on a machine with a browser. Use the compatible Python interpreter from the setup above.

   ```sh
   TRACKER_PYTHON=.venv/bin/python  # Substitute your compatible interpreter if needed.
   install -d -m 700 "$HOME/.config/job-application-agent"
   TOKEN_FILE="$HOME/.config/job-application-agent/gmail-token.json"
   "$TRACKER_PYTHON" -m job_tracker.main --authorize "$CLIENT_FILE" --token-file "$TOKEN_FILE"
   ```

   Sign in and grant read-only access. The command creates a new mode-0600 credential file containing an offline refresh token. Use a fresh token path if reauthorizing. External OAuth apps in Testing generally receive [refresh tokens that expire after seven days](https://developers.google.com/identity/protocols/oauth2#expiration). Consult [Google's restricted-scope requirements](https://developers.google.com/workspace/gmail/api/auth/scopes) before distributing or deploying beyond personal testing.

3. Run a bounded scan. Use a Gmail label you already have to narrow the scan, or omit `--query` for the built-in 90-day search for application, interview, and offer subjects. The default cap is 25 messages.

   ```sh
   "$TRACKER_PYTHON" -m job_tracker.main --dry-run --token-file "$TOKEN_FILE" --query 'label:jobs newer_than:30d' --limit 20
   ```

## Save and view applications

Once the dry run looks right, use `--sync` with the same search. This creates or updates the tracker database and prints only counts. Use `--show` to see the saved rows without contacting Gmail.

```sh
"$TRACKER_PYTHON" -m job_tracker.main --sync --query 'label:tracker.test' --limit 10
"$TRACKER_PYTHON" -m job_tracker.main --show
```

The database defaults to `~/.config/job-application-agent/applications.sqlite`. To use another location, pass `--db /path/to/applications.sqlite` to both commands. The parent directory must exist. Keep the database private.

Matching uses company and job ID when available. A missing ID can fall back to an unambiguous company and role match. Distinct job IDs are kept separate, and conflicting matches and dates are counted for review. Without a job ID, two applications to the same company and role can be indistinguishable.

The latest message by Gmail arrival time determines the status. Earlier updates can fill in missing fields. When a date is missing, the tracker uses the UTC Gmail receipt date of the earliest matched confirmation or the latest rejection update. An explicit date takes precedence. A rejection alone does not supply an application date. The database holds the six application fields and the receipt metadata needed for ordering and date fallback. It contains no Gmail message IDs, email bodies, or sender details. Review outcomes are reported as counts only.

## Run as a cloud job

Build the container. Configure your job platform to inject the token JSON from its secret manager as a read-only file, for example `/run/secrets/gmail-token.json`. The file must be readable by container UID 10001 with mode 0400 or 0600. Use a private job with outbound HTTPS to Gmail and Google OAuth, and pass the arguments shown below to its entry point.

```sh
docker build -t job-application-agent .
```

```text
--dry-run --token-file /run/secrets/gmail-token.json --limit 25
```

For a container smoke test without credentials, run `docker run --rm job-application-agent --help`. The image excludes local environments and credential files. The job refreshes its access token in memory and exits after the scan. For sync mode, pass `--sync` and place the database on a persistent volume with SQLite-compatible file locking and write access for UID 10001. Keep credentials out of source control, image layers, and build arguments.

## What the dry run measures

- `classified` means a message had one recognized status and passed the six-field application schema. The rules extract company and position from explicit labels or supported confirmation and rejection templates. The other fields are job ID, status, application date, and rejection date. Dates require explicit ISO dates.
- `needs_review` covers ambiguous or incomplete evidence, invalid fields, and unreadable payloads. `no_signal` means the parser found neither a status signal nor an explicit field label. These are counts only.
- This pass classifies messages independently. A Gmail thread can contribute multiple messages. Unstructured templates, different languages, and messages whose text is available only through an attachment reference may need manual review. Silence never counts as a rejection.
- Message content, addresses, contact details, and Gmail IDs are never emitted or saved by the dry run. It processes inline plain text and HTML, skips attachment parts, and never fetches an attachment separately. Keep HTTP tracing and request-body capture disabled on the job.
- `--validate synthetic.json` validates and displays an explicitly supplied synthetic record. Tests use synthetic Gmail payloads and a fake API.
