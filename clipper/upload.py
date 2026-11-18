"""YouTube Data API v3 upload + OAuth credential handling."""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional

from . import config


def load_credentials():
    """Load saved creds from token.json, refreshing if needed. Returns creds or None."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not config.TOKEN_FILE.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(config.TOKEN_FILE), config.YOUTUBE_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        config.TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def run_oauth_flow(open_browser: bool = False):
    """Run the installed-app OAuth flow; save token.json. Returns creds.

    With open_browser=False it prints the consent URL and waits on a local
    server for the redirect — so the consent click can be automated.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not config.CLIENT_SECRETS.exists():
        raise FileNotFoundError(
            f"Missing OAuth client secrets at {config.CLIENT_SECRETS}. Run project setup first."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.CLIENT_SECRETS), config.YOUTUBE_SCOPES
    )
    creds = flow.run_local_server(
        host="localhost",
        port=config.OAUTH_PORT,
        open_browser=open_browser,
        authorization_prompt_message="AUTH_URL>>> {url}",
        success_message="Authorized. You can close this tab and return to the terminal.",
    )
    config.TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_service():
    from googleapiclient.discovery import build

    creds = load_credentials()
    if creds is None:
        raise RuntimeError("No credentials. Run: python main.py auth")
    return build("youtube", "v3", credentials=creds)


def get_channel(youtube=None) -> dict:
    youtube = youtube or get_service()
    resp = youtube.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        return {}
    sn = items[0]["snippet"]
    return {"id": items[0]["id"], "title": sn.get("title")}


def upload_video(
    file_path: Path,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    privacy: str = "private",
    category_id: str = "22",
    made_for_kids: bool = False,
    youtube=None,
) -> dict:
    """Resumable upload. Returns the created video resource. privacy: private|unlisted|public."""
    from googleapiclient.http import MediaFileUpload

    youtube = youtube or get_service()
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }
    media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True, mimetype="video/*")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[upload] {int(status.progress() * 100)}%")
    return response
