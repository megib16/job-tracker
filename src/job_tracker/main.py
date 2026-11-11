import argparse
import json
import logging
from pathlib import Path
import sqlite3
import sys

from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException

from .agent import run_agent
from .anthropic_evaluator import AnthropicEvaluator
from .email_evaluator import EvaluationError
from .config import SHEETS_SCOPES, SPREADSHEET_ID, TOKEN_DIR
from .gmail_auth import AuthorizationError, authorize, load_credentials
from .gmail_client import gmail_messages
from .models import Application
from .openai_evaluator import OpenAIEvaluator
from .store import list_applications
from .sheets_client import create_tracker, spreadsheets
from .pipeline import dry_run, sync

DEFAULT_QUERY = (
    'newer_than:90d -in:sent -in:drafts '
    '{subject:application subject:interview subject:"job offer"}'
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Privacy-first job application tracker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate", metavar="JSON_FILE", type=Path, help="Validate a synthetic application record")
    mode.add_argument("--authorize", metavar="DESKTOP_CLIENT_JSON", type=Path, help="Authorize Gmail read-only access")
    mode.add_argument("--dry-run", action="store_true", help="Scan Gmail and print aggregate counts")
    mode.add_argument("--sync", action="store_true", help="Scan Gmail and update the private tracker")
    mode.add_argument("--show", action="store_true", help="Display the saved application records as JSON")
    mode.add_argument("--authorize-sheets", metavar="DESKTOP_CLIENT_JSON", type=Path,
                      help="Authorize access to the dedicated tracker spreadsheet")
    mode.add_argument("--create-sheet", action="store_true", help="Create the dedicated tracker spreadsheet")
    mode.add_argument("--run-agent", action="store_true", help="Backfill or sync Gmail to Google Sheets")
    provider = parser.add_mutually_exclusive_group()
    provider.add_argument("--use-openai", dest="evaluator", action="store_const", const=OpenAIEvaluator,
                          help="Send minimized email text to OpenAI for dry run or agent mode")
    provider.add_argument("--use-anthropic", dest="evaluator", action="store_const", const=AnthropicEvaluator,
                          help="Send minimized email text to Claude for dry run or agent mode")
    parser.add_argument("--spreadsheet-id", default=SPREADSHEET_ID)
    parser.add_argument("--sheets-token-file", type=Path, default=TOKEN_DIR / "sheets-token.json")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--token-file", type=Path, default=TOKEN_DIR / "gmail-token.json")
    parser.add_argument("--query", help="Gmail search expression for dry run or sync")
    parser.add_argument("--limit", type=int, default=25, help="Maximum messages to scan, from 1 to 500")
    args = parser.parse_args(argv)
    if args.evaluator and not (args.dry_run or args.run_agent):
        parser.error("The AI provider option requires --dry-run or --run-agent")
    query = args.query if args.query is not None else ("newer_than:90d -in:drafts" if args.evaluator else DEFAULT_QUERY)
    if not 1 <= args.limit <= 500 or not query.strip():
        parser.error("Use a nonempty query and a limit between 1 and 500")

    if args.validate:
        try:
            record = Application.model_validate_json(args.validate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("Could not validate the synthetic application record.", file=sys.stderr)
            return 1
        print("Valid application record:", record.model_dump_json(indent=2))
        return 0

    default_database = "applications.sqlite"
    if args.run_agent:
        default_database = "agent-ai.sqlite" if args.evaluator else "agent.sqlite"
    database = (args.db or TOKEN_DIR / default_database).expanduser()
    if args.run_agent and not args.spreadsheet_id:
        parser.error("--run-agent needs the ID returned by --create-sheet")
    if args.show:
        try:
            records = list_applications(database)
        except (OSError, ValueError, sqlite3.Error):
            print("Could not open the private tracker. Run a sync first and check the database path and permissions.", file=sys.stderr)
            return 1
        print(json.dumps([record.model_dump(mode="json") for record in records], indent=2, ensure_ascii=False))
        return 0

    if not (args.authorize or args.dry_run or args.sync or args.authorize_sheets or args.create_sheet or args.run_agent):
        parser.print_help()
        return 0

    # Client library warnings can include request URLs and mailbox search terms.
    for name in ("googleapiclient", "google.auth", "google_auth_oauthlib", "oauthlib", "requests_oauthlib", "urllib3"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)

    try:
        evaluator = args.evaluator.from_environment() if args.evaluator else None
        if args.authorize:
            authorize(args.authorize.expanduser(), args.token_file.expanduser())
            print("Saved Gmail read-only credentials.")
        elif args.authorize_sheets:
            authorize(args.authorize_sheets.expanduser(), args.sheets_token_file.expanduser(), SHEETS_SCOPES)
            print("Saved credentials for the dedicated tracker spreadsheet.")
        elif args.create_sheet or args.run_agent:
            sheets_credentials = load_credentials(args.sheets_token_file.expanduser(), SHEETS_SCOPES)
            sheets = spreadsheets(sheets_credentials)
            if args.create_sheet:
                print(json.dumps(create_tracker(sheets), sort_keys=True))
            else:
                gmail_credentials = load_credentials(args.token_file.expanduser())
                result = run_agent(
                    gmail_messages(gmail_credentials), sheets, args.spreadsheet_id, database, evaluator=evaluator,
                )
                print(json.dumps(result, sort_keys=True))
        else:
            credentials = load_credentials(args.token_file.expanduser())
            messages = gmail_messages(credentials)
            result = (
                sync(messages, query, args.limit, database) if args.sync
                else dry_run(messages, query, args.limit, evaluator=evaluator)
            )
            print(json.dumps(result, sort_keys=True))
    except EvaluationError as error:
        print(f"AI extraction failed. {error}", file=sys.stderr)
        return 1
    except sqlite3.Error:
        print("Could not update the private tracker database.", file=sys.stderr)
        return 1
    except AuthorizationError as error:
        print(f"Authorization failed. {error}", file=sys.stderr)
        return 1
    except FileExistsError:
        print("The token file already exists. Choose a fresh path to authorize again.", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError, GoogleAuthError,
            HttpError, HttpLib2Error, OAuth2Error, RequestException):
        if args.create_sheet or args.run_agent:
            print("Agent operation failed. Check both token files, the Sheets API, and spreadsheet access.", file=sys.stderr)
        else:
            print("Gmail operation failed. Check the token file, OAuth consent, Gmail API access, and network.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
