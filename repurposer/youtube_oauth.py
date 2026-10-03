from pathlib import Path

from django.conf import settings
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import AuthorizedSession
from googleapiclient.discovery import build

from .models import YouTubeCredential


# YouTube upload + account read + analytics ke liye
# required permissions
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def get_client_secret_file():
    """
    Find the Google OAuth client secret JSON file.
    """

    credentials_directory = (
        Path(settings.BASE_DIR) / "credentials"
    )

    json_files = list(
        credentials_directory.glob("*.json")
    )

    if not json_files:
        raise FileNotFoundError(
            "No OAuth client secret JSON file found "
            "inside the credentials folder."
        )

    return json_files[0]


def create_youtube_oauth_flow():
    """
    Create the Google YouTube OAuth flow.
    """

    client_secret_file = get_client_secret_file()

    flow = Flow.from_client_secrets_file(
        str(client_secret_file),
        scopes=YOUTUBE_SCOPES,
        redirect_uri=(
            "http://127.0.0.1:8000/youtube/callback/"
        ),
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