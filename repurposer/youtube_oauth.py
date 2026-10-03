import json
import os
from pathlib import Path

from django.conf import settings
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import AuthorizedSession

from .models import YouTubeCredential


# YouTube upload + account read + analytics ke liye
# required permissions
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def get_client_config():
    """
    Get Google OAuth client configuration.

    Priority:
    1. Render/production environment variables
    2. Local credentials/*.json file

    This keeps local development working while allowing
    Render deployment without uploading the OAuth JSON file
    to GitHub.
    """

    google_client_id = os.getenv(
        "GOOGLE_CLIENT_ID"
    )

    google_client_secret = os.getenv(
        "GOOGLE_CLIENT_SECRET"
    )

    if google_client_id and google_client_secret:
        return {
            "web": {
                "client_id": google_client_id,
                "client_secret": google_client_secret,
                "auth_uri": (
                    "https://accounts.google.com/o/oauth2/auth"
                ),
                "token_uri": (
                    "https://oauth2.googleapis.com/token"
                ),
            }
        }

    # Local development fallback
    credentials_directory = (
        Path(settings.BASE_DIR) / "credentials"
    )

    json_files = list(
        credentials_directory.glob("*.json")
    )

    if not json_files:
        raise FileNotFoundError(
            "Google OAuth credentials not found. "
            "Set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET environment variables "
            "or place the OAuth client JSON file inside "
            "the credentials folder."
        )

    with open(
        json_files[0],
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def get_redirect_uri():
    """
    Get the OAuth callback URL.

    Production:
    Set YOUTUBE_REDIRECT_URI in Render.

    Local:
    Falls back to the local development callback.
    """

    return os.getenv(
        "YOUTUBE_REDIRECT_URI",
        "http://127.0.0.1:8000/youtube/callback/",
    )


def create_youtube_oauth_flow():
    """
    Create the Google YouTube OAuth flow.
    """

    client_config = get_client_config()

    flow = Flow.from_client_config(
        client_config,
        scopes=YOUTUBE_SCOPES,
        redirect_uri=get_redirect_uri(),
    )

    return flow


def get_youtube_authorization_url():
    """
    Generate the Google authorization URL
    and return the PKCE code verifier.
    """

    flow = create_youtube_oauth_flow()

    authorization_url, state = (
        flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
    )

    return (
        authorization_url,
        state,
        flow.code_verifier,
    )


def exchange_code_for_credentials(request):
    """
    Exchange Google's authorization code
    for OAuth credentials using the PKCE verifier.
    """

    state = request.session.get(
        "youtube_oauth_state"
    )

    code_verifier = request.session.get(
        "youtube_code_verifier"
    )

    if not state:
        raise ValueError(
            "YouTube OAuth state is missing from the session."
        )

    if not code_verifier:
        raise ValueError(
            "YouTube OAuth code verifier is missing "
            "from the session."
        )

    code = request.GET.get("code")

    if not code:
        raise ValueError(
            "YouTube authorization code is missing."
        )

    flow = create_youtube_oauth_flow()

    flow.code_verifier = code_verifier

    flow.fetch_token(
        code=code
    )

    return flow.credentials


def get_youtube_channel_info(credentials):
    """
    Get the authenticated YouTube channel information
    using Google's authorized HTTP session.
    """

    session = AuthorizedSession(credentials)

    response = session.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={
            "part": "snippet",
            "mine": "true",
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    channels = data.get("items", [])

    if not channels:
        raise ValueError(
            "No YouTube channel was found "
            "for this Google account."
        )

    channel = channels[0]

    return {
        "channel_id": channel["id"],
        "channel_name": channel["snippet"]["title"],
    }


def save_youtube_credentials(
    credentials,
    channel_info
):
    """
    Save YouTube OAuth credentials
    and channel information in the database.

    If the channel already exists,
    update its OAuth credentials instead
    of creating a duplicate record.
    """

    token_json = credentials.to_json()

    youtube_credential, created = (
        YouTubeCredential.objects.update_or_create(
            channel_id=channel_info["channel_id"],
            defaults={
                "channel_name": channel_info["channel_name"],
                "token_json": token_json,
                "is_active": True,
            },
        )
    )

    if created:
        print(
            "New YouTube channel credential saved:"
        )
    else:
        print(
            "Existing YouTube channel credential "
            "updated successfully:"
        )

    print(
        {
            "channel_id": youtube_credential.channel_id,
            "channel_name": youtube_credential.channel_name,
            "created": created,
        }
    )

    return youtube_credential