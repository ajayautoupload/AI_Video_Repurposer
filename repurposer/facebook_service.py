import os
import time

import requests


FACEBOOK_GRAPH_VERSION = "v23.0"

FACEBOOK_VIDEO_UPLOAD_BASE_URL = (
    f"https://graph-video.facebook.com/"
    f"{FACEBOOK_GRAPH_VERSION}"
)

MAX_NETWORK_RETRIES = 3
RETRY_WAIT_SECONDS = 5


def _facebook_response_data(response):
    """
    Safely read Facebook JSON response.
    """

    try:
        return response.json()

    except ValueError:
        return {
            "raw_response": response.text
        }


def _raise_facebook_error(
    message,
    response,
):
    """
    Convert a Facebook HTTP error into a readable
    Python exception.
    """

    error_data = _facebook_response_data(
        response
    )

    raise ValueError(
        f"{message}: {error_data}"
    )


def _is_retryable_network_error(exc):
    """
    Return True when the error is a temporary
    network/DNS/connection problem.
    """

    return isinstance(
        exc,
        (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ),
    )


def start_resumable_video_upload(
    page_id,
    page_access_token,
    file_size,
):
    """
    Start a resumable Facebook Page video upload.
    """

    for attempt in range(
        1,
        MAX_NETWORK_RETRIES + 1,
    ):

        try:

            response = requests.post(
                f"{FACEBOOK_VIDEO_UPLOAD_BASE_URL}/"
                f"{page_id}/videos",
                data={
                    "upload_phase": "start",
                    "file_size": str(file_size),
                    "access_token": page_access_token,
                },
                timeout=60,
            )

            if not response.ok:
                _raise_facebook_error(
                    "Facebook resumable upload start failed",
                    response,
                )

            data = _facebook_response_data(
                response
            )

            required_fields = [
                "video_id",
                "upload_session_id",
                "start_offset",
                "end_offset",
            ]

            for field in required_fields:

                if field not in data:
                    raise ValueError(
                        "Facebook resumable upload start "
                        f"did not return '{field}'. "
                        f"Facebook response: {data}"
                    )

            return data

        except requests.exceptions.RequestException as exc:

            if (
                attempt
                >= MAX_NETWORK_RETRIES
            ):
                raise

            print(
                "Facebook upload session start "
                "encountered a temporary network error."
            )

            print(
                "Retry:",
                f"{attempt + 1}/"
                f"{MAX_NETWORK_RETRIES}",
            )

            print(
                "Error:",
                exc,
            )

            time.sleep(
                RETRY_WAIT_SECONDS
            )


def transfer_video_chunk(
    page_id,
    page_access_token,
    upload_session_id,
    start_offset,
    video_chunk,
):
    """
    Upload one chunk of the video.

    Temporary network errors are retried.
    """

    for attempt in range(
        1,
        MAX_NETWORK_RETRIES + 1,
    ):

        try:

            response = requests.post(
                f"{FACEBOOK_VIDEO_UPLOAD_BASE_URL}/"
                f"{page_id}/videos",
                data={
                    "upload_phase": "transfer",
                    "upload_session_id": (
                        upload_session_id
                    ),
                    "start_offset": str(
                        start_offset
                    ),
                    "access_token": page_access_token,
                },
                files={
                    "video_file_chunk": (
                        "video_chunk.mp4",
                        video_chunk,
                        "application/octet-stream",
                    ),
                },
                timeout=180,
            )

            if not response.ok:
                _raise_facebook_error(
                    "Facebook video chunk upload failed",
                    response,
                )

            data = _facebook_response_data(
                response
            )

            if "start_offset" not in data:
                raise ValueError(
                    "Facebook chunk upload did not "
                    "return the next start_offset. "
                    f"Facebook response: {data}"
                )

            return data

        except requests.exceptions.RequestException as exc:

            if (
                attempt
                >= MAX_NETWORK_RETRIES
            ):
                raise

            print(
                "Facebook video chunk encountered "
                "a temporary network error."
            )

            print(
                "Current chunk offset:",
                start_offset,
            )

            print(
                "Retry:",
                f"{attempt + 1}/"
                f"{MAX_NETWORK_RETRIES}",
            )

            print(
                "Error:",
                exc,
            )

            time.sleep(
                RETRY_WAIT_SECONDS
            )


def finish_resumable_video_upload(
    page_id,
    page_access_token,
    upload_session_id,
    title="",
    description="",
):
    """
    Finish the Facebook resumable upload
    and publish the video immediately.
    """

    for attempt in range(
        1,
        MAX_NETWORK_RETRIES + 1,
    ):

        try:

            response = requests.post(
                f"{FACEBOOK_VIDEO_UPLOAD_BASE_URL}/"
                f"{page_id}/videos",
                data={
                    "upload_phase": "finish",
                    "upload_session_id": (
                        upload_session_id
                    ),
                    "title": title,
                    "description": description,
                    "published": "true",
                    "access_token": page_access_token,
                },
                timeout=120,
            )

            if not response.ok:
                _raise_facebook_error(
                    "Facebook resumable upload finish failed",
                    response,
                )

            data = _facebook_response_data(
                response
            )

            if data.get("success") is not True:

                raise ValueError(
                    "Facebook video upload did not "
                    f"finish successfully. "
                    f"Facebook response: {data}"
                )

            return data

        except requests.exceptions.RequestException as exc:

            if (
                attempt
                >= MAX_NETWORK_RETRIES
            ):
                raise

            print(
                "Facebook upload finish encountered "
                "a temporary network error."
            )

            print(
                "Retry:",
                f"{attempt + 1}/"
                f"{MAX_NETWORK_RETRIES}",
            )

            print(
                "Error:",
                exc,
            )

            time.sleep(
                RETRY_WAIT_SECONDS
            )


def finish_scheduled_resumable_video_upload(
    page_id,
    page_access_token,
    upload_session_id,
    scheduled_publish_time,
    title="",
    description="",
):
    """
    Finish a Facebook resumable upload and schedule
    the video for future publication.

    scheduled_publish_time:
        Unix timestamp in seconds.
    """

    if not isinstance(
        scheduled_publish_time,
        int,
    ):
        raise ValueError(
            "scheduled_publish_time must be "
            "an integer Unix timestamp."
        )

    current_timestamp = int(
        time.time()
    )

    if scheduled_publish_time <= current_timestamp:
        raise ValueError(
            "scheduled_publish_time must be "
            "in the future."
        )

    for attempt in range(
        1,
        MAX_NETWORK_RETRIES + 1,
    ):

        try:

            response = requests.post(
                f"{FACEBOOK_VIDEO_UPLOAD_BASE_URL}/"
                f"{page_id}/videos",
                data={
                    "upload_phase": "finish",
                    "upload_session_id": (
                        upload_session_id
                    ),
                    "title": title,
                    "description": description,

                    # Important:
                    # Facebook must NOT publish immediately.
                    "published": "false",

                    # Facebook native scheduling.
                    "scheduled_publish_time": str(
                        scheduled_publish_time
                    ),

                    "access_token": page_access_token,
                },
                timeout=120,
            )

            if not response.ok:
                _raise_facebook_error(
                    "Facebook scheduled video upload finish failed",
                    response,
                )

            data = _facebook_response_data(
                response
            )

            if data.get("success") is not True:

                raise ValueError(
                    "Facebook scheduled video upload "
                    "did not finish successfully. "
                    f"Facebook response: {data}"
                )

            return data

        except requests.exceptions.RequestException as exc:

            if (
                attempt
                >= MAX_NETWORK_RETRIES
            ):
                raise

            print(
                "Facebook scheduled upload finish "
                "encountered a temporary network error."
            )

            print(
                "Retry:",
                f"{attempt + 1}/"
                f"{MAX_NETWORK_RETRIES}",
            )

            print(
                "Error:",
                exc,
            )

            time.sleep(
                RETRY_WAIT_SECONDS
            )


def upload_video_to_facebook(
    page_id,
    page_access_token,
    video_path,
    title="",
    description="",
):
    """
    Upload a Facebook Page video using
    resumable/chunked upload.

    Large videos are divided into chunks so that
    the complete video is not sent in one request.

    This function publishes the video immediately.
    """

    if not os.path.isfile(video_path):

        raise ValueError(
            "Facebook upload failed because "
            "the video file was not found: "
            f"{video_path}"
        )

    file_size = os.path.getsize(
        video_path
    )

    if file_size <= 0:

        raise ValueError(
            "Facebook upload failed because "
            "the video file is empty: "
            f"{video_path}"
        )

    print(
        "Starting Facebook resumable upload..."
    )

    print(
        "Video file:",
        video_path,
    )

    print(
        "Video size:",
        round(
            file_size / (1024 * 1024),
            2,
        ),
        "MB",
    )

    start_data = (
        start_resumable_video_upload(
            page_id=page_id,
            page_access_token=(
                page_access_token
            ),
            file_size=file_size,
        )
    )

    video_id = start_data[
        "video_id"
    ]

    upload_session_id = start_data[
        "upload_session_id"
    ]

    start_offset = int(
        start_data[
            "start_offset"
        ]
    )

    end_offset = int(
        start_data[
            "end_offset"
        ]
    )

    print(
        "Facebook upload session started."
    )

    print(
        "Video ID:",
        video_id,
    )

    print(
        "Upload session ID:",
        upload_session_id,
    )

    print(
        "First chunk:",
        start_offset,
        "to",
        end_offset,
    )

    with open(
        video_path,
        "rb",
    ) as video_file:

        while start_offset < file_size:

            chunk_size = (
                end_offset
                - start_offset
            )

            if chunk_size <= 0:

                raise ValueError(
                    "Facebook returned an "
                    "invalid upload chunk range: "
                    f"{start_offset} - "
                    f"{end_offset}"
                )

            video_file.seek(
                start_offset
            )

            video_chunk = (
                video_file.read(
                    chunk_size
                )
            )

            if not video_chunk:

                raise ValueError(
                    "Could not read the next "
                    "Facebook video chunk."
                )

            actual_chunk_size = len(
                video_chunk
            )

            chunk_end = (
                start_offset
                + actual_chunk_size
            )

            print(
                "Uploading Facebook chunk:",
                f"{start_offset} - "
                f"{chunk_end}",
                f"({round(actual_chunk_size / (1024 * 1024), 2)} MB)",
            )

            transfer_data = (
                transfer_video_chunk(
                    page_id=page_id,
                    page_access_token=(
                        page_access_token
                    ),
                    upload_session_id=(
                        upload_session_id
                    ),
                    start_offset=(
                        start_offset
                    ),
                    video_chunk=(
                        video_chunk
                    ),
                )
            )

            next_start_offset = int(
                transfer_data[
                    "start_offset"
                ]
            )

            next_end_offset = int(
                transfer_data.get(
                    "end_offset",
                    file_size,
                )
            )

            if (
                next_start_offset
                <= start_offset
            ):

                raise ValueError(
                    "Facebook did not advance "
                    "the upload offset. "
                    f"Current offset: "
                    f"{start_offset}, "
                    f"next offset: "
                    f"{next_start_offset}"
                )

            start_offset = (
                next_start_offset
            )

            end_offset = min(
                next_end_offset,
                file_size,
            )

            print(
                "Facebook upload progress:",
                f"{round((start_offset / file_size) * 100, 2)}%",
            )

    print(
        "All Facebook video chunks "
        "uploaded successfully."
    )

    finish_data = (
        finish_resumable_video_upload(
            page_id=page_id,
            page_access_token=(
                page_access_token
            ),
            upload_session_id=(
                upload_session_id
            ),
            title=title,
            description=description,
        )
    )

    print(
        "Facebook video published successfully."
    )

    return {
        "video_id": video_id,
        "response": finish_data,
    }


def upload_scheduled_video_to_facebook(
    page_id,
    page_access_token,
    video_path,
    scheduled_publish_time,
    title="",
    description="",
):
    """
    Upload a Facebook Page video using
    resumable/chunked upload and schedule it
    for future publication.

    scheduled_publish_time:
        Unix timestamp in seconds.
    """

    if not os.path.isfile(video_path):

        raise ValueError(
            "Facebook scheduled upload failed because "
            "the video file was not found: "
            f"{video_path}"
        )

    file_size = os.path.getsize(
        video_path
    )

    if file_size <= 0:

        raise ValueError(
            "Facebook scheduled upload failed because "
            "the video file is empty: "
            f"{video_path}"
        )

    if not isinstance(
        scheduled_publish_time,
        int,
    ):
        raise ValueError(
            "scheduled_publish_time must be "
            "an integer Unix timestamp."
        )

    current_timestamp = int(
        time.time()
    )

    if scheduled_publish_time <= current_timestamp:
        raise ValueError(
            "scheduled_publish_time must be "
            "in the future."
        )

    print(
        "Starting Facebook scheduled "
        "resumable upload..."
    )

    print(
        "Video file:",
        video_path,
    )

    print(
        "Video size:",
        round(
            file_size / (1024 * 1024),
            2,
        ),
        "MB",
    )

    start_data = (
        start_resumable_video_upload(
            page_id=page_id,
            page_access_token=(
                page_access_token
            ),
            file_size=file_size,
        )
    )

    video_id = start_data[
        "video_id"
    ]

    upload_session_id = start_data[
        "upload_session_id"
    ]

    start_offset = int(
        start_data[
            "start_offset"
        ]
    )

    end_offset = int(
        start_data[
            "end_offset"
        ]
    )

    print(
        "Facebook scheduled upload session started."
    )

    print(
        "Video ID:",
        video_id,
    )

    print(
        "Upload session ID:",
        upload_session_id,
    )

    with open(
        video_path,
        "rb",
    ) as video_file:

        while start_offset < file_size:

            chunk_size = (
                end_offset
                - start_offset
            )

            if chunk_size <= 0:

                raise ValueError(
                    "Facebook returned an "
                    "invalid upload chunk range: "
                    f"{start_offset} - {end_offset}"
                )

            video_file.seek(
                start_offset
            )

            video_chunk = (
                video_file.read(
                    chunk_size
                )
            )

            if not video_chunk:

                raise ValueError(
                    "Could not read the next "
                    "Facebook video chunk."
                )

            actual_chunk_size = len(
                video_chunk
            )

            chunk_end = (
                start_offset
                + actual_chunk_size
            )

            print(
                "Uploading Facebook scheduled chunk:",
                f"{start_offset} - {chunk_end}",
                f"({round(actual_chunk_size / (1024 * 1024), 2)} MB)",
            )

            transfer_data = (
                transfer_video_chunk(
                    page_id=page_id,
                    page_access_token=(
                        page_access_token
                    ),
                    upload_session_id=(
                        upload_session_id
                    ),
                    start_offset=(
                        start_offset
                    ),
                    video_chunk=(
                        video_chunk
                    ),
                )
            )

            next_start_offset = int(
                transfer_data[
                    "start_offset"
                ]
            )

            next_end_offset = int(
                transfer_data.get(
                    "end_offset",
                    file_size,
                )
            )

            if (
                next_start_offset
                <= start_offset
            ):

                raise ValueError(
                    "Facebook did not advance "
                    "the upload offset. "
                    f"Current offset: "
                    f"{start_offset}, "
                    f"next offset: "
                    f"{next_start_offset}"
                )

            start_offset = (
                next_start_offset
            )

            end_offset = min(
                next_end_offset,
                file_size,
            )

            print(
                "Facebook scheduled upload progress:",
                f"{round((start_offset / file_size) * 100, 2)}%",
            )

    print(
        "All Facebook scheduled video chunks "
        "uploaded successfully."
    )

    finish_data = (
        finish_scheduled_resumable_video_upload(
            page_id=page_id,
            page_access_token=(
                page_access_token
            ),
            upload_session_id=(
                upload_session_id
            ),
            scheduled_publish_time=(
                scheduled_publish_time
            ),
            title=title,
            description=description,
        )
    )

    print(
        "Facebook video scheduled successfully."
    )

    return {
        "video_id": video_id,
        "scheduled_publish_time": (
            scheduled_publish_time
        ),
        "response": finish_data,
    }


def publish_video_to_facebook(
    page_id,
    page_access_token,
    video_path,
    title="",
    description="",
):
    """
    Complete Facebook Page video publishing workflow.
    """

    return upload_video_to_facebook(
        page_id=page_id,
        page_access_token=page_access_token,
        video_path=video_path,
        title=title,
        description=description,
    )