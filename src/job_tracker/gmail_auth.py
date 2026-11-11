"""Desktop consent and read-only credentials for unattended jobs."""
import json
import os
from pathlib import Path
from stat import S_ISREG
from webbrowser import Error as BrowserError

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from oauthlib.oauth2 import AccessDeniedError, OAuth2Error
from requests.exceptions import RequestException

from .config import GMAIL_SCOPES

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class AuthorizationError(Exception):
    """An actionable authorization error safe to print without credentials."""


def authorize(client_file: Path, token_file: Path, scopes=GMAIL_SCOPES):
    try:
        with client_file.open(encoding="utf-8") as handle:
            client = json.load(handle)
    except FileNotFoundError:
        raise AuthorizationError("The OAuth client JSON was not found. Select the downloaded file in Finder.") from None
    except (OSError, ValueError):
        raise AuthorizationError("Could not read the OAuth client JSON. Use the original Google download.") from None

    installed = client.get("installed") if isinstance(client, dict) else None
    if not isinstance(installed, dict) or (
        installed.get("token_uri") != TOKEN_URI or installed.get("auth_uri") != AUTH_URI
        or not installed.get("client_id") or not installed.get("client_secret")
    ):
        raise AuthorizationError("This file is not a Google Desktop app OAuth client JSON.")
    if token_file.exists():
        raise FileExistsError("Choose a fresh token path")
    flow = InstalledAppFlow.from_client_config(client, scopes, autogenerate_code_verifier=True)
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1", port=0, prompt="consent", include_granted_scopes="false",
            authorization_prompt_message=None, timeout_seconds=180,
        )
    except WSGITimeoutError:
        raise AuthorizationError(
            "Google sign-in did not return within three minutes. Check the browser for a Google error."
        ) from None
    except AccessDeniedError:
        raise AuthorizationError(
            "Google denied access. If the OAuth app is in Testing, confirm your Gmail account is a test user."
        ) from None
    except BrowserError:
        raise AuthorizationError("Could not open the default browser for Google sign-in.") from None
    except Warning:
        raise AuthorizationError("Google returned a different permission than the one requested.") from None
    except OAuth2Error:
        raise AuthorizationError("Google rejected the OAuth exchange. Check that the Desktop client is active.") from None
    except RequestException:
        raise AuthorizationError("Could not connect to Google's OAuth server.") from None
    except OSError:
        raise AuthorizationError("Could not start the local Google sign-in callback.") from None

    granted = credentials.granted_scopes
    if granted is None or set(granted) != set(scopes):
        raise AuthorizationError("Google did not confirm exactly the permission requested.")
    if not credentials.refresh_token:
        raise AuthorizationError("Google did not issue an offline token. Retry and complete the consent screen.")

    # Exclusive creation prevents replacement of an existing secret.
    info = json.loads(credentials.to_json())
    fields = ("client_id", "client_secret", "refresh_token", "scopes", "token_uri")
    payload = json.dumps({key: info[key] for key in fields})
    try:
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError:
        raise
    except OSError:
        raise AuthorizationError("Google sign-in succeeded, but the token file could not be saved.") from None


def load_credentials(token_file: Path, scopes=GMAIL_SCOPES):
    with token_file.open(encoding="utf-8") as handle:
        mode = os.fstat(handle.fileno()).st_mode
        if not S_ISREG(mode) or mode & 0o077:
            raise ValueError("Expected a private token file (mode 0600 or 0400)")
        info = json.load(handle)
    if set(info.get("scopes") or []) != set(scopes) or info.get("token_uri") != TOKEN_URI:
        if scopes == GMAIL_SCOPES:
            raise ValueError("Expected a Google Gmail read-only token")
        raise ValueError("Expected a Google token with the requested scope")
    # Always refresh in memory and ignore any cached bearer token in the file.
    info.pop("token", None)
    info.pop("expiry", None)
    return Credentials.from_authorized_user_info(info, scopes=scopes)
