import json
import os
import time

import requests


X_API_BASE_URL = "https://api.x.com"

X_MEDIA_INITIALIZE_URL = (
    f"{X_API_BASE_URL}/2/media/upload/initialize"
)

X_MEDIA_UPLOAD_URL = (
    f"{X_API_BASE_URL}/2/media/upload"
)

X_MEDIA_APPEND_URL = (
    f"{X_API_BASE_URL}/2/media/upload"
)

X_MEDIA_FINALIZE_URL = (
    f"{X_API_BASE_URL}/2/media/upload"
)

X_CREATE_POST_URL = (
    f"{X_API_BASE_URL}/2/tweets"
)

X_CHUNK_SIZE = 4 * 1024 * 1024

X_PROCESSING_TIMEOUT = 15 * 60

X_DEFAULT_STATUS_WAIT = 2


def _get_access_token(x_credential):
    if not x_credential:
        raise ValueError(
            "X credential was not provided."
        )

    if not x_credential.token_json:
        raise ValueError(
            "X credential does not contain token data."
        )

    try:
        token_data = json.loads(
            x_credential.token_json
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "X credential token data is invalid."
        ) from exc

    access_token = token_data.get(
        "access_token"
    )

    if not access_token:
        raise ValueError(
            "X access token was not found."
        )

    return access_token


def _auth_headers(access_token):
    return {
        "Authorization": (
            f"Bearer {access_token}"
        ),
        "Accept": "application/json",
        "User-Agent": (
            "AI-Video-Repurposer"
        ),
    }


def _raise_x_error(response, action):
    if response.ok:
        return

    try:
        error_data = response.json()
    except ValueError:
        error_data = {}

    error_message = (
        error_data.get("detail")
        or error_data.get("title")
        or error_data.get("message")
        or response.text
        or "Unknown X API error."
    )

    errors = error_data.get("errors")

    if errors and isinstance(
        errors,
        list,
    ):
        error_messages = []

        for error in errors:
            if isinstance(
                error,
                dict,
            ):
                message = (
                    error.get("detail")
                    or error.get("message")
                    or error.get("title")
                )

                if message:
                    error_messages.append(
                        str(message)
                    )

        if error_messages:
            error_message = (
                " | ".join(
                    error_messages
                )
            )

    raise ValueError(
        f"X {action} failed "
        f"(HTTP {response.status_code}): "
        f"{error_message}"
    )


# ============================================================
# INIT
# ============================================================

def initialize_video_upload(
    access_token,
    total_bytes,
):
    headers = _auth_headers(
        access_token
    )

    headers["Content-Type"] = (
        "application/json"
    )

    payload = {
        "media_type": "video/mp4",
        "total_bytes": int(
            total_bytes
        ),
        "media_category": (
            "tweet_video"
        ),
    }

    response = requests.post(
        X_MEDIA_INITIALIZE_URL,
        headers=headers,
        json=payload,
        timeout=60,
    )

    _raise_x_error(
        response,
        "video initialization",
    )

    response_data = response.json()

    media_data = (
        response_data.get("data")
        or {}
    )

    media_id = media_data.get("id")

    if not media_id:
        raise ValueError(
            "X did not return a media ID "
            "during video initialization."
        )

    return str(media_id)


# ============================================================
# APPEND
# ============================================================

def append_video_chunk(
    access_token,
    media_id,
    chunk,
    segment_index,
):
    headers = _auth_headers(
        access_token
    )

    upload_url = (
        f"{X_MEDIA_APPEND_URL}/"
        f"{media_id}/append"
    )

    data = {
        "segment_index": str(
            segment_index
        )
    }

    files = {
        "media": (
            "video_chunk.mp4",
            chunk,
            "application/octet-stream",
        )
    }

    response = requests.post(
        upload_url,
        headers=headers,
        data=data,
        files=files,
        timeout=120,
    )

    _raise_x_error(
        response,
        (
            "video chunk upload "
            f"(segment {segment_index})"
        ),
    )


# ============================================================
# FINALIZE
# ============================================================

def finalize_video_upload(
    access_token,
    media_id,
):
    headers = _auth_headers(
        access_token
    )

    finalize_url = (
        f"{X_MEDIA_FINALIZE_URL}/"
        f"{media_id}/finalize"
    )

    response = requests.post(
        finalize_url,
        headers=headers,
        timeout=60,
    )

    _raise_x_error(
        response,
        "video finalization",
    )

    response_data = response.json()

    return (
        response_data.get("data")
        or {}
    )


# ============================================================
# STATUS
# ============================================================

def get_video_upload_status(
    access_token,
    media_id,
):
    headers = _auth_headers(
        access_token
    )

    params = {
        "command": "STATUS",
        "media_id": str(
            media_id
        ),
    }

    response = requests.get(
        X_MEDIA_UPLOAD_URL,
        headers=headers,
        params=params,
        timeout=60,
    )

    _raise_x_error(
        response,
        "video processing status request",
    )

    response_data = response.json()

    return (
        response_data.get("data")
        or {}
    )


def wait_for_video_processing(
    access_token,
    media_id,
    timeout_seconds=(
        X_PROCESSING_TIMEOUT
    ),
):
    started_at = time.monotonic()

    while True:
        elapsed = (
            time.monotonic()
            - started_at
        )

        if elapsed >= timeout_seconds:
            raise TimeoutError(
                "X video processing timed out."
            )

        status_data = (
            get_video_upload_status(
                access_token,
                media_id,
            )
        )

        processing_info = (
            status_data.get(
                "processing_info"
            )
        )

        if not processing_info:
            return status_data

        state = processing_info.get(
            "state"
        )

        print(
            f"X video processing state: "
            f"{state}"
        )

        if state == "succeeded":
            return status_data

        if state == "failed":
            error_data = (
                processing_info.get(
                    "error"
                )
                or {}
            )

            error_message = (
                error_data.get("message")
                or error_data.get("detail")
                or "X video processing failed."
            )

            raise ValueError(
                "X video processing failed: "
                f"{error_message}"
            )

        check_after = (
            processing_info.get(
                "check_after_secs"
            )
            or X_DEFAULT_STATUS_WAIT
        )

        check_after = max(
            1,
            int(check_after),
        )

        remaining = (
            timeout_seconds
            - elapsed
        )

        time.sleep(
            min(
                check_after,
                max(
                    1,
                    int(remaining),
                ),
            )
        )


# ============================================================
# CREATE POST
# ============================================================

def create_x_post(
    access_token,
    media_id,
    text="",
):
    headers = _auth_headers(
        access_token
    )

    headers["Content-Type"] = (
        "application/json"
    )

    payload = {
        "media": {
            "media_ids": [
                str(media_id)
            ]
        }
    }

    clean_text = (
        text.strip()
        if text
        else ""
    )

    if clean_text:
        payload["text"] = clean_text

    response = requests.post(
        X_CREATE_POST_URL,
        headers=headers,
        json=payload,
        timeout=60,
    )

    _raise_x_error(
        response,
        "post creation",
    )

    response_data = response.json()

    post_data = (
        response_data.get("data")
        or {}
    )

    post_id = post_data.get("id")

    if not post_id:
        raise ValueError(
            "X post was created but "
            "no post ID was returned."
        )

    return post_data


# ============================================================
# FULL VIDEO UPLOAD
# ============================================================

def upload_video_to_x(
    access_token,
    video_path,
):
    video_path = os.fspath(
        video_path
    )

    if not os.path.isfile(
        video_path
    ):
        raise FileNotFoundError(
            f"X video file not found: "
            f"{video_path}"
        )

    total_bytes = os.path.getsize(
        video_path
    )

    if total_bytes <= 0:
        raise ValueError(
            "X video file is empty."
        )

    print(
        "Starting X video upload: "
        f"{os.path.basename(video_path)}"
    )

    print(
        f"X video size: "
        f"{total_bytes:,} bytes"
    )

    # --------------------------------------------------------
    # INIT
    # --------------------------------------------------------

    media_id = (
        initialize_video_upload(
            access_token,
            total_bytes,
        )
    )

    print(
        "X upload initialized. "
        f"Media ID: {media_id}"
    )

    # --------------------------------------------------------
    # APPEND
    # --------------------------------------------------------

    segment_index = 0

    bytes_sent = 0

    with open(
        video_path,
        "rb",
    ) as video_file:

        while True:
            chunk = video_file.read(
                X_CHUNK_SIZE
            )

            if not chunk:
                break

            append_video_chunk(
                access_token,
                media_id,
                chunk,
                segment_index,
            )

            bytes_sent += len(
                chunk
            )

            print(
                "X upload progress: "
                f"{bytes_sent:,}/"
                f"{total_bytes:,} bytes"
            )

            segment_index += 1

    print(
        "X upload chunks completed: "
        f"{segment_index}"
    )

    # --------------------------------------------------------
    # FINALIZE
    # --------------------------------------------------------

    finalize_data = (
        finalize_video_upload(
            access_token,
            media_id,
        )
    )

    processing_info = (
        finalize_data.get(
            "processing_info"
        )
    )

    if processing_info:
        print(
            "X video processing started."
        )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    wait_for_video_processing(
        access_token,
        media_id,
    )

    print(
        "X video processing completed."
    )

    return str(media_id)


# ============================================================
# PUBLISH VIDEO
# ============================================================

def publish_video_to_x(
    x_credential,
    video_path,
    text="",
):
    access_token = (
        _get_access_token(
            x_credential
        )
    )

    media_id = (
        upload_video_to_x(
            access_token,
            video_path,
        )
    )

    post_data = create_x_post(
        access_token,
        media_id,
        text=text,
    )

    post_id = post_data.get(
        "id"
    )

    print(
        "X post created successfully. "
        f"Post ID: {post_id}"
    )

    return {
        "success": True,
        "media_id": media_id,
        "post_id": post_id,
        "text": post_data.get(
            "text",
            text,
        ),
    }