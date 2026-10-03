import time
import uuid
from pathlib import Path

import requests


INSTAGRAM_GRAPH_VERSION = "v23.0"


# ============================================================
# Instagram Login API
# ============================================================

INSTAGRAM_GRAPH_BASE_URL = (
    f"https://graph.instagram.com/"
    f"{INSTAGRAM_GRAPH_VERSION}"
)


# ============================================================
# Facebook Login for Business API
# ============================================================

FACEBOOK_GRAPH_BASE_URL = (
    f"https://graph.facebook.com/"
    f"{INSTAGRAM_GRAPH_VERSION}"
)


INSTAGRAM_RESUMABLE_UPLOAD_URL = (
    f"https://rupload.facebook.com/"
    f"ig-api-upload/"
    f"{INSTAGRAM_GRAPH_VERSION}"
)


class InstagramContainerStatusError(ValueError):
    """
    Error raised while checking an Instagram
    Reel container status.

    is_transient=True means Instagram returned
    a temporary API error and the status request
    can be retried.
    """

    def __init__(
        self,
        message,
        is_transient=False,
        status_code=None,
    ):
        super().__init__(message)

        self.is_transient = is_transient
        self.status_code = status_code


class InstagramContainerProcessingError(ValueError):
    """
    Raised when Instagram accepts the container
    but later reports that the media processing
    failed.
    """

    def __init__(
        self,
        message,
        container_id=None,
        video_url=None,
    ):
        super().__init__(message)

        self.container_id = container_id
        self.video_url = video_url


# ============================================================
# OLD PUBLIC URL FLOW
# ============================================================

# ============================================================
# PUBLIC MEDIA URL HELPERS
# ============================================================

def _fresh_video_url(video_url):
    """
    Add a unique cache-buster so Meta gets a fresh media URL
    on every container attempt. Django will ignore this query string
    when serving the media file.
    """
    separator = "&" if "?" in video_url else "?"
    return f"{video_url}{separator}ig_upload={uuid.uuid4().hex}"


def _validate_public_video_url(video_url):
    """
    Make a lightweight external HTTP request before creating a Meta
    container. Meta's Reels API fetches video_url itself, so the URL
    must return the actual MP4 bytes over public HTTPS.
    """
    try:
        response = requests.get(
            video_url,
            headers={"Range": "bytes=0-1023"},
            stream=True,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ValueError(
            "Instagram video URL could not be reached from the app server: "
            f"{exc}"
        ) from exc

    try:
        if response.status_code not in (200, 206):
            raise ValueError(
                "Instagram video URL is not publicly reachable. "
                f"HTTP status: {response.status_code}\n"
                f"URL: {video_url}"
            )

        content_type = (
            response.headers.get("Content-Type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )

        if content_type and content_type not in {
            "video/mp4",
            "application/octet-stream",
        }:
            raise ValueError(
                "Instagram video URL returned an unexpected Content-Type: "
                f"{content_type}. Expected video/mp4."
            )
    finally:
        response.close()


def create_instagram_reel_container(
    instagram_user_id,
    access_token,
    video_url,
    caption="",
    share_to_feed=True,
    use_facebook_graph=False,
):
    """
    Existing public-URL Instagram Login flow.

    Kept for backward compatibility.

    This function is NOT used by the new
    Facebook Login for Business resumable flow.
    """

    base_url = (
        FACEBOOK_GRAPH_BASE_URL
        if use_facebook_graph
        else INSTAGRAM_GRAPH_BASE_URL
    )

    response = requests.post(
        f"{base_url}/"
        f"{instagram_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": str(
                share_to_feed
            ).lower(),
            "access_token": access_token,
        },
        timeout=30,
    )

    if not response.ok:

        try:
            error_data = response.json()

        except ValueError:
            error_data = {
                "raw_response": response.text
            }

        raise ValueError(
            "Instagram Reel container creation failed: "
            f"{error_data}"
        )

    data = response.json()

    container_id = data.get("id")

    if not container_id:
        raise ValueError(
            "Instagram Reel container ID was not returned. "
            f"Instagram response: {data}"
        )

    return container_id


# ============================================================
# NEW FACEBOOK LOGIN FOR BUSINESS FLOW
# ============================================================

def create_instagram_resumable_reel_container(
    instagram_user_id,
    access_token,
    caption="",
    share_to_feed=True,
):
    """
    Create an Instagram Reel container for a
    Facebook Login for Business resumable upload.

    The local video is uploaded separately to
    Meta's resumable upload endpoint.

    Important:

    Facebook Login for Business uses:

        graph.facebook.com

    with a Page access token.
    """

    response = requests.post(
        f"{FACEBOOK_GRAPH_BASE_URL}/"
        f"{instagram_user_id}/media",
        data={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": str(
                share_to_feed
            ).lower(),
            "access_token": access_token,
        },
        timeout=30,
    )

    if not response.ok:

        try:
            error_data = response.json()

        except ValueError:
            error_data = {
                "raw_response": response.text
            }

        raise ValueError(
            "Instagram resumable Reel container "
            f"creation failed: {error_data}"
        )

    data = response.json()

    container_id = data.get("id")

    if not container_id:
        raise ValueError(
            "Instagram resumable Reel container ID "
            "was not returned. "
            f"Instagram response: {data}"
        )

    return container_id


def upload_video_to_instagram_resumable(
    container_id,
    access_token,
    video_path,
):
    """
    Upload a local MP4 file directly to Meta's
    Instagram resumable upload endpoint.

    The local video does not need a public URL.
    """

    video_path = Path(
        video_path
    )

    if not video_path.exists():

        raise FileNotFoundError(
            f"Instagram upload video not found: "
            f"{video_path}"
        )

    if not video_path.is_file():

        raise ValueError(
            f"Instagram upload path is not a file: "
            f"{video_path}"
        )

    file_size = (
        video_path.stat().st_size
    )

    if file_size <= 0:

        raise ValueError(
            f"Instagram upload file is empty: "
            f"{video_path}"
        )

    upload_url = (
        f"{INSTAGRAM_RESUMABLE_UPLOAD_URL}/"
        f"{container_id}"
    )

    headers = {
        "Authorization": (
            f"OAuth {access_token}"
        ),
        "offset": "0",
        "file_size": str(file_size),
        "Content-Type": "application/octet-stream",
    }

    with video_path.open(
        "rb"
    ) as video_file:

        response = requests.post(
            upload_url,
            headers=headers,
            data=video_file,
            timeout=600,
        )

    if not response.ok:

        try:
            error_data = response.json()

        except ValueError:
            error_data = {
                "raw_response": response.text
            }

        raise ValueError(
            "Instagram resumable video upload failed: "
            f"{error_data}"
        )

    try:
        response_data = response.json()

    except ValueError:
        response_data = {
            "raw_response": response.text
        }

    return {
        "container_id": container_id,
        "file_size": file_size,
        "response": response_data,
    }


# ============================================================
# CONTAINER STATUS
# ============================================================

def get_instagram_container_status(
    container_id,
    access_token,
    use_facebook_graph=False,
):
    """
    Check Instagram Reel container processing status.

    For Facebook Login for Business, use:

        graph.facebook.com

    For the old Instagram Login flow, use:

        graph.instagram.com
    """

    if use_facebook_graph:

        base_url = (
            FACEBOOK_GRAPH_BASE_URL
        )

    else:

        base_url = (
            INSTAGRAM_GRAPH_BASE_URL
        )

    response = requests.get(
        f"{base_url}/"
        f"{container_id}",
        params={
            "fields": "status_code,status",
            "access_token": access_token,
        },
        timeout=30,
    )

    if not response.ok:

        try:
            error_data = response.json()

        except ValueError:
            error_data = {
                "raw_response": response.text
            }

        error_info = error_data.get(
            "error",
            {}
        )

        is_transient = (
            error_info.get(
                "is_transient"
            )
            is True
        )

        raise InstagramContainerStatusError(
            "Instagram container status request failed: "
            f"{error_data}",
            is_transient=is_transient,
            status_code=response.status_code,
        )

    data = response.json()

    return data


def wait_for_instagram_container(
    container_id,
    access_token,
    video_url=None,
    max_attempts=5,
    wait_seconds=60,
    use_facebook_graph=False,
):
    """
    Wait until Instagram finishes processing
    the Reel container.
    """

    for attempt in range(
        max_attempts
    ):

        try:

            status_data = (
                get_instagram_container_status(
                    container_id=container_id,
                    access_token=access_token,
                    use_facebook_graph=use_facebook_graph,
                )
            )

        except InstagramContainerStatusError as exc:

            if (
                exc.is_transient
                and attempt < max_attempts - 1
            ):

                print(
                    "Instagram returned a temporary "
                    "container status error. "
                    f"Retrying in {wait_seconds} seconds..."
                )

                time.sleep(
                    wait_seconds
                )

                continue

            raise

        status_code = status_data.get(
            "status_code"
        )

        print(
            "Instagram container status:",
            status_code,
        )

        if status_code == "FINISHED":

            return status_data

        if status_code == "ERROR":

            raise InstagramContainerProcessingError(
                "Instagram Reel processing failed.\n"
                f"Container ID: {container_id}\n"
                f"Video URL: {video_url}\n"
                f"Instagram response: {status_data}",
                container_id=container_id,
                video_url=video_url,
            )

        if status_code == "EXPIRED":

            raise InstagramContainerProcessingError(
                "Instagram Reel container expired.\n"
                f"Container ID: {container_id}\n"
                f"Video URL: {video_url}\n"
                f"Instagram response: {status_data}",
                container_id=container_id,
                video_url=video_url,
            )

        if attempt < max_attempts - 1:

            time.sleep(
                wait_seconds
            )

    raise TimeoutError(
        "Instagram Reel processing timed out.\n"
        f"Container ID: {container_id}\n"
        f"Video URL: {video_url}"
    )


# ============================================================
# PUBLISH
# ============================================================

def publish_instagram_reel(
    instagram_user_id,
    access_token,
    container_id,
    use_facebook_graph=False,
):
    """
    Publish a processed Instagram Reel.

    Facebook Login for Business uses the
    Facebook Graph API host.
    """

    if use_facebook_graph:

        base_url = (
            FACEBOOK_GRAPH_BASE_URL
        )

    else:

        base_url = (
            INSTAGRAM_GRAPH_BASE_URL
        )

    response = requests.post(
        f"{base_url}/"
        f"{instagram_user_id}/media_publish",
        data={
            "creation_id": container_id,
            "access_token": access_token,
        },
        timeout=30,
    )

    if not response.ok:

        try:
            error_data = response.json()

        except ValueError:
            error_data = {
                "raw_response": response.text
            }

        raise ValueError(
            "Instagram Reel publishing failed: "
            f"{error_data}"
        )

    data = response.json()

    media_id = data.get("id")

    if not media_id:

        raise ValueError(
            "Instagram published media ID was not returned. "
            f"Instagram response: {data}"
        )

    return data


# ============================================================
# OLD PUBLIC URL PUBLISHING FLOW
# ============================================================

def upload_reel_to_instagram(
    instagram_user_id,
    access_token,
    video_url,
    caption="",
    share_to_feed=True,
    max_container_attempts=2,
    use_facebook_graph=True,
    retry_delay_seconds=90,
):
    """
    Existing public URL publishing workflow.

    Kept for backward compatibility.

    Meta processes video_url asynchronously. Error 2207077 can be a
    media-ingest failure on Meta's side, so the workflow uses a fresh
    URL on each container attempt and avoids rapid-fire retries.
    """

    _validate_public_video_url(video_url)

    last_processing_error = None

    for container_attempt in range(
        1,
        max_container_attempts + 1,
    ):

        print(
            "Instagram Reel container attempt "
            f"{container_attempt}/"
            f"{max_container_attempts}"
        )

        attempt_video_url = _fresh_video_url(video_url)

        print(
            "Instagram upload URL attempt:",
            attempt_video_url,
        )

        container_id = (
            create_instagram_reel_container(
                instagram_user_id=instagram_user_id,
                access_token=access_token,
                video_url=attempt_video_url,
                caption=caption,
                share_to_feed=share_to_feed,
                use_facebook_graph=use_facebook_graph,
            )
        )

        print(
            "Instagram Reel container created:",
            container_id,
        )

        try:

            wait_for_instagram_container(
                container_id=container_id,
                access_token=access_token,
                video_url=attempt_video_url,
                use_facebook_graph=use_facebook_graph,
            )

        except InstagramContainerProcessingError as exc:

            last_processing_error = exc

            if (
                container_attempt
                < max_container_attempts
            ):

                print(
                    "Instagram container processing "
                    "failed. Creating a fresh container..."
                )

                print(
                    "Waiting before the next Instagram container attempt "
                    f"({retry_delay_seconds} seconds)..."
                )
                time.sleep(retry_delay_seconds)

                continue

            raise

        except TimeoutError as exc:

            last_processing_error = exc

            if (
                container_attempt
                < max_container_attempts
            ):

                print(
                    "Instagram container processing "
                    "timed out. Creating a fresh container..."
                )

                print(
                    "Waiting before the next Instagram container attempt "
                    f"({retry_delay_seconds} seconds)..."
                )
                time.sleep(retry_delay_seconds)

                continue

            raise

        published_data = (
            publish_instagram_reel(
                instagram_user_id=instagram_user_id,
                access_token=access_token,
                container_id=container_id,
                use_facebook_graph=use_facebook_graph,
            )
        )

        print(
            "Instagram Reel published successfully:",
            published_data,
        )

        return {
            "container_id": container_id,
            "media_id": published_data.get("id"),
            "response": published_data,
        }

    if last_processing_error:

        raise last_processing_error

    raise ValueError(
        "Instagram Reel publishing failed after "
        f"{max_container_attempts} container attempts."
    )


# ============================================================
# NEW LOCAL FILE RESUMABLE PUBLISHING FLOW
# ============================================================

def upload_local_reel_to_instagram(
    instagram_user_id,
    access_token,
    video_path,
    caption="",
    share_to_feed=True,
    max_container_attempts=3,
):
    """
    Complete Instagram Reel publishing workflow
    using:

        Facebook Login for Business
        +
        Page access token
        +
        local MP4
        +
        resumable upload

    Flow:

        1. Create resumable container
        2. Upload local MP4
        3. Check processing status
        4. Retry with a fresh container if needed
        5. Publish after FINISHED

    No public video URL is required.
    """

    video_path = Path(
        video_path
    )

    if not video_path.exists():

        raise FileNotFoundError(
            f"Instagram local video not found: "
            f"{video_path}"
        )

    last_processing_error = None

    for container_attempt in range(
        1,
        max_container_attempts + 1,
    ):

        print(
            "Instagram resumable Reel container "
            "attempt "
            f"{container_attempt}/"
            f"{max_container_attempts}"
        )

        container_id = (
            create_instagram_resumable_reel_container(
                instagram_user_id=instagram_user_id,
                access_token=access_token,
                caption=caption,
                share_to_feed=share_to_feed,
            )
        )

        print(
            "Instagram resumable container created:",
            container_id,
        )

        try:

            upload_result = (
                upload_video_to_instagram_resumable(
                    container_id=container_id,
                    access_token=access_token,
                    video_path=video_path,
                )
            )

            print(
                "Instagram resumable video upload "
                "completed:",
                upload_result,
            )

            wait_for_instagram_container(
                container_id=container_id,
                access_token=access_token,
                use_facebook_graph=True,
            )

        except InstagramContainerProcessingError as exc:

            last_processing_error = exc

            if (
                container_attempt
                < max_container_attempts
            ):

                print(
                    "Instagram resumable container "
                    "processing failed. Creating "
                    "a fresh container..."
                )

                time.sleep(5)

                continue

            raise

        except TimeoutError as exc:

            last_processing_error = exc

            if (
                container_attempt
                < max_container_attempts
            ):

                print(
                    "Instagram resumable container "
                    "processing timed out. Creating "
                    "a fresh container..."
                )

                time.sleep(5)

                continue

            raise

        published_data = (
            publish_instagram_reel(
                instagram_user_id=instagram_user_id,
                access_token=access_token,
                container_id=container_id,
                use_facebook_graph=True,
            )
        )

        print(
            "Instagram Reel published successfully "
            "using resumable upload:",
            published_data,
        )

        return {
            "container_id": container_id,
            "media_id": published_data.get("id"),
            "response": published_data,
        }

    if last_processing_error:

        raise last_processing_error

    raise ValueError(
        "Instagram local Reel publishing failed "
        "after "
        f"{max_container_attempts} container attempts."
    )