# Job Application Agent

A Python CLI that reads Gmail with read-only access and tracks job applications in SQLite or Google Sheets. It records the company, position, job ID, status, application date, and rejection date. Optional Claude and OpenAI modes also add a short notes field.

## Install

Use Python 3.11 or newer on macOS or Linux. From the repository root, create a virtual environment for a fresh clone, or activate your existing compatible environment.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
job-tracker --help
```

## Connect Gmail and Google Sheets

1. In Google Cloud, enable the Gmail API and the Google Sheets API. Configure OAuth consent and create a **Desktop app** OAuth client. If the app is in Testing, add your Gmail account as a test user. Download the client JSON to a private location outside the repository. [Google's Gmail setup guide](https://developers.google.com/workspace/gmail/api/quickstart/python)
2. Set `CLIENT_FILE` to the path of your downloaded JSON and authorize Gmail. This opens a browser for read-only consent and saves the token in a private directory.

   ```sh
   CLIENT_FILE="/absolute/path/to/desktop-client.json"
   install -d -m 700 "$HOME/.config/job-application-agent"
   job-tracker --authorize "$CLIENT_FILE"
   ```

3. Try a small dry run. This uses the local parser and prints counts.

   ```sh
   job-tracker --dry-run --limit 20
   ```

   To restrict the scan to a Gmail label you already use, add `--query 'label:your-label'`.

4. If you want a Google Sheet, grant the separate per-file permission and create the tracker. The second command prints the spreadsheet link and ID. Copy the ID for the next step. [Google's Sheets permission scopes](https://developers.google.com/workspace/sheets/api/scopes)

   ```sh
   job-tracker --authorize-sheets "$CLIENT_FILE"
   job-tracker --create-sheet
   ```

Google OAuth refresh tokens issued to External apps in Testing generally expire after seven days. The authorization commands expect a fresh token path when you reauthorize. [Google's token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration)

## Backfill and update the Sheet

Run the tracker with the spreadsheet ID from setup.

```sh
SPREADSHEET_ID="your-spreadsheet-id"
job-tracker --run-agent --spreadsheet-id "$SPREADSHEET_ID"
```

The first run searches the previous three calendar months for likely application emails using English and German search terms. It publishes validated results to **Applications**. Subsequent runs resume from the last successful publication with a two-day overlap for late mail. Keep the same database and spreadsheet ID across runs.

### Use Claude or OpenAI

Supply `ANTHROPIC_API_KEY` through your environment or secret manager for Claude. To use OpenAI, supply `OPENAI_API_KEY` and replace `--use-anthropic` with `--use-openai`. You can override the provider's default model with `JOB_TRACKER_ANTHROPIC_MODEL` or `JOB_TRACKER_OPENAI_MODEL`.

```sh
job-tracker --dry-run --use-anthropic --limit 10
job-tracker --run-agent --use-anthropic --spreadsheet-id "$SPREADSHEET_ID"
```

AI mode searches by date, includes sent mail, and publishes to **AI Applications** with a **Notes** column. It uses a separate SQLite database and checkpoint. An AI dry run sends cleaned text to the selected provider and prints counts. The full AI backfill can incur API charges. Keep personal notes on a separate tab, since the tracker rewrites the cells it manages.

## Local-only tracking

Use the local parser to save application records to SQLite and display the saved rows.

```sh
job-tracker --sync --limit 25
job-tracker --show
```

The local-only database is `~/.config/job-application-agent/applications.sqlite`. Google Sheets runs use `agent.sqlite` or `agent-ai.sqlite` in the same directory. Use `--db` to select a different path and create its parent directory first.

Matching prefers the company and job ID, then falls back to an unambiguous company and role match. Explicit dates take precedence. The tracker can use Gmail timestamps from a matching confirmation, submission, or rejection to fill missing dates. Review counts flag messages the tracker could not classify or match safely.

## Run periodically

Run the same `--run-agent` command from a scheduler on an always-on host. Provide the spreadsheet ID, Google tokens, and any model API key through the job's environment or private files. Keep the appropriate SQLite database on persistent storage so the job can resume after a restart.

The included Dockerfile packages the Python CLI. Check that the image starts without using credentials.

```sh
docker build -t job-application-agent .
docker run --rm job-application-agent --help
```

For a live container job, provide the private tokens as files readable by container UID 10001 and use a writable persistent volume for the database. The CLI accepts `--token-file`, `--sheets-token-file`, and `--db` for these paths.

## Data and credentials

- The tracker reads Gmail and processes message bodies in memory. It skips attachments and saves application fields and tracking metadata in SQLite and, when enabled, Google Sheets.
- AI mode strips common greetings, signatures, and recognizable contact details before sending text. Redaction is best effort, so submitted text may still contain personal information. [Anthropic API retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data) · [OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data)
- Keep API keys, OAuth client files, refresh tokens, local environment files, and tracker databases out of source control. The included `.env.example` provides safe configuration defaults.

## Development

The tests use synthetic emails and simulated API responses.

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
```
