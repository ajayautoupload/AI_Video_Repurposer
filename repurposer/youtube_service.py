from pathlib import Path
import json
import time
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

import httplib2
from google_auth_httplib2 import AuthorizedHttp

from django.conf import settings

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# ============================================================
# IPv4-only network helper
# ============================================================

@contextmanager
def force_ipv4():
    """
    Temporarily force Python socket DNS resolution to IPv4.

    This is used only while communicating with YouTube/Google.
    """

    original_getaddrinfo = socket.getaddrinfo

    def ipv4_getaddrinfo(
        host,
        port,
        family=0,
        type=0,
        proto=0,
        flags=0
    ):
        return original_getaddrinfo(
            host,
            port,
            socket.AF_INET,
            type,
            proto,
            flags
        )

    socket.getaddrinfo = ipv4_getaddrinfo

    try:
        yield

    finally:
        socket.getaddrinfo = original_getaddrinfo


# ============================================================
# YouTube Download
# ============================================================

def download_youtube_video(youtube_url):
    """
    Download a YouTube video using yt-dlp.

    Returns:
        str: Downloaded video file path.
    """

    output_directory = (
        Path(settings.MEDIA_ROOT) / "youtube"
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True
    )

    output_template = str(
        output_directory / "%(title)s.%(ext)s"
    )

    ydl_options = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",

        "merge_output_format": "mp4",

        "outtmpl": output_template,

        "noplaylist": True,

        "windowsfilenames": True,

        # ----------------------------------------------------
        # YouTube anti-bot compatibility
        # ----------------------------------------------------
        "impersonate": ImpersonateTarget(),
    }

    with yt_dlp.YoutubeDL(
        ydl_options
    ) as ydl:

        info = ydl.extract_info(
            youtube_url,
            download=True
        )

        downloaded_file = (
            ydl.prepare_filename(info)
        )

        downloaded_file = Path(
            downloaded_file
        )

        if (
            downloaded_file.suffix.lower()
            != ".mp4"
        ):

            mp4_file = (
                downloaded_file.with_suffix(
                    ".mp4"
                )
            )

            if mp4_file.exists():
                downloaded_file = mp4_file

        if not downloaded_file.exists():
            raise FileNotFoundError(
                "YouTube video was downloaded, "
                "but the final MP4 file was not found."
            )

        return str(downloaded_file)


# ============================================================
# USA YouTube Schedule
# ============================================================

YOUTUBE_SCHEDULE_TIMEZONE = (
    ZoneInfo("America/New_York")
)


YOUTUBE_DAILY_SLOTS = [
    (9, 0),
    (14, 0),
    (19, 0),
]


def get_next_youtube_schedule_slots(
    number_of_slots
):
    """
    Return the next available YouTube publishing
    slots using USA Eastern Time.

    Daily schedule:

        09:00 ET
        14:00 ET
        19:00 ET
    """

    if number_of_slots <= 0:
        return []

    now_utc = datetime.now(
        timezone.utc
    )

    now_et = now_utc.astimezone(
        YOUTUBE_SCHEDULE_TIMEZONE
    )

    current_date = (
        now_et.date()
    )

    slots = []

    while len(slots) < number_of_slots:

        for hour, minute in (
            YOUTUBE_DAILY_SLOTS
        ):

            candidate = datetime(
                year=current_date.year,
                month=current_date.month,
                day=current_date.day,
                hour=hour,
                minute=minute,
                tzinfo=YOUTUBE_SCHEDULE_TIMEZONE,
            )

            if candidate > now_et:

                slots.append(
                    candidate
                )

                if len(slots) >= number_of_slots:
                    break

        current_date = (
            current_date + timedelta(
                days=1
            )
        )

    return slots


# ============================================================
# YouTube Upload
# ============================================================

def upload_video_to_youtube(
    video_path,
    token_json,
    title,
    description="",
    tags=None,
    privacy_status="private",
    publish_at=None
):
    """
    Upload an MP4 video to YouTube.

    Supports:
    - Normal uploads
    - Scheduled uploads
    - Resumable uploads
    - IPv4-only Google connection
    - YouTube 308 resumable-upload handling
    - Network retry handling
    """

    video_path = Path(
        video_path
    )

    if not video_path.exists():
        raise FileNotFoundError(
            f"Video file not found: {video_path}"
        )

    if tags is None:
        tags = []

    # --------------------------------------------------------
    # Scheduled videos must remain private
    # --------------------------------------------------------

    if publish_at is not None:

        privacy_status = "private"

        if publish_at.tzinfo is None:

            raise ValueError(
                "publish_at must be timezone-aware."
            )

        now_utc = datetime.now(
            timezone.utc
        )

        publish_at_utc = (
            publish_at.astimezone(
                timezone.utc
            )
        )

        if publish_at_utc <= now_utc:

            raise ValueError(
                "publish_at must be in the future."
            )

        publish_at_value = (
            publish_at_utc
            .isoformat()
            .replace(
                "+00:00",
                "Z"
            )
        )

    else:

        publish_at_value = None

    # --------------------------------------------------------
    # Convert stored JSON into Google credentials
    # --------------------------------------------------------

    token_data = json.loads(
        token_json
    )

    credentials = (
        Credentials.from_authorized_user_info(
            token_data
        )
    )

    # --------------------------------------------------------
    # Create HTTP client
    # --------------------------------------------------------

    http = httplib2.Http(
        timeout=180
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # YouTube resumable uploads use HTTP 308 responses
    # to indicate that the upload should continue.
    #
    # httplib2 can incorrectly treat 308 as a redirect and
    # require a Location header.
    #
    # Remove 308 from redirect handling so google-api-client
    # can correctly process the resumable upload response.
    # --------------------------------------------------------

    if hasattr(
        http,
        "redirect_codes"
    ):

        http.redirect_codes = (
            http.redirect_codes - {308}
        )

    authorized_http = AuthorizedHttp(
        credentials,
        http=http
    )

    # --------------------------------------------------------
    # Create YouTube API client
    # --------------------------------------------------------

    youtube = build(
        "youtube",
        "v3",
        http=authorized_http,
        cache_discovery=False,
    )

    # --------------------------------------------------------
    # Video metadata
    # --------------------------------------------------------

    status_data = {
        "privacyStatus": privacy_status,

        "selfDeclaredMadeForKids": False,
    }

    if publish_at_value is not None:

        status_data["publishAt"] = (
            publish_at_value
        )

    body = {
        "snippet": {
            "title": title,

            "description": description,

            "tags": tags,

            "categoryId": "22",
        },

        "status": status_data,
    }

    # --------------------------------------------------------
    # MP4 file upload
    # --------------------------------------------------------

    media = MediaFileUpload(
        str(video_path),

        mimetype="video/mp4",

        resumable=True,

        chunksize=5 * 1024 * 1024,
    )

    request = youtube.videos().insert(
        part="snippet,status",

        body=body,

        media_body=media,
    )

    # --------------------------------------------------------
    # Resumable upload
    # --------------------------------------------------------

    response = None

    max_retries = 5

    retry_delay = 10

    # --------------------------------------------------------
    # Force IPv4 for Google upload
    # --------------------------------------------------------

    with force_ipv4():

        print(
            "YouTube upload network mode: IPv4"
        )

        while response is None:

            retry_count = 0

            while True:

                try:

                    status, response = (
                        request.next_chunk()
                    )

                    if status:

                        progress = int(
                            status.progress() * 100
                        )

                        print(
                            f"YouTube upload progress: "
                            f"{progress}%"
                        )

                    break

                except (
                    OSError,
                    socket.timeout,
                    TimeoutError,
                    ConnectionError,
                ) as exc:

                    retry_count += 1

                    print(
                        "YouTube upload network error: "
                        f"{exc}"
                    )

                    if retry_count > max_retries:

                        print(
                            "YouTube upload failed after "
                            f"{max_retries} retries."
                        )

                        raise

                    print(
                        f"Retrying YouTube upload "
                        f"({retry_count}/{max_retries}) "
                        f"in {retry_delay} seconds..."
                    )

                    time.sleep(
                        retry_delay
                    )

    # --------------------------------------------------------
    # Final response
    # --------------------------------------------------------

    return {
        "video_id": response["id"],

        "url": (
            "https://www.youtube.com/watch?v="
            + response["id"]
        ),

        "title": response["snippet"]["title"],

        "privacy_status": response["status"][
            "privacyStatus"
        ],

        "publish_at": response["status"].get(
            "publishAt"
        ),
    }