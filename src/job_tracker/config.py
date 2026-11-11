import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SHEETS_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_DIR = Path(os.getenv("JOB_TRACKER_TOKEN_DIR", Path.home() / ".config" / "job-application-agent"))
SPREADSHEET_ID = os.getenv("JOB_TRACKER_SPREADSHEET_ID", "")
OLLAMA_MODEL = os.getenv("JOB_TRACKER_MODEL", "gemma3:4b")
