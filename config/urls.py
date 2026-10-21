"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views.
"""

import hashlib
import mimetypes
import os
from email.utils import formatdate

from django.conf import settings
from django.contrib import admin
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotFound,
    StreamingHttpResponse,
)
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.urls import include, path 
from django.utils import timezone
from repurposer import auth_views


def build_file_etag(file_size, modified_timestamp):
    """
    Build a stable ETag from file size and
    last-modified timestamp.
    """

    value = (
        f"{file_size}:"
        f"{modified_timestamp}"
    )

    return hashlib.md5(
        value.encode("utf-8")
    ).hexdigest()


def add_media_headers(
    response,
    content_type,
    file_size,
    modified_timestamp,
    etag,
):
    """
    Add HTTP headers useful for video delivery.
    """

    response["Content-Type"] = (
        content_type
    )

    response["Content-Length"] = str(
        len(response.content)
    )

    response["Accept-Ranges"] = "bytes"

    response["Cache-Control"] = "private, no-store"

    response["ETag"] = (
        f'"{etag}"'
    )

    response["Last-Modified"] = (
        formatdate(
            modified_timestamp,
            usegmt=True,
        )
    )

    response[
        "X-Content-Type-Options"
    ] = "nosniff"

    return response


def serve_media_file(
    request,
    file_path,
):
    """
    Serve media files with reliable HTTP Range support.

    File bodies are streamed in bounded chunks so large videos do not
    need to be loaded into a Django worker's memory all at once.
    """

    if request.method not in [
        "GET",
        "HEAD",
    ]:
        response = HttpResponse(
            "Method not allowed.",
            status=405,
        )

        response["Allow"] = "GET, HEAD"

        return response

    media_root = os.path.realpath(
        str(settings.MEDIA_ROOT)
    )

    requested_path = os.path.realpath(
        os.path.join(
            media_root,
            file_path,
        )
    )

    # --------------------------------
    # Security check
    # --------------------------------

    if (
        requested_path != media_root
        and not requested_path.startswith(
            media_root + os.sep
        )
    ):
        return HttpResponseNotFound(
            "Media file not found."
        )

    # --------------------------------
    # File existence check
    # --------------------------------

    if not os.path.isfile(
        requested_path
    ):
        return HttpResponseNotFound(
            "Media file not found."
        )

    relative_path = os.path.relpath(requested_path, media_root).replace(os.sep, "/")
    signed = False
    token = request.GET.get("token", "")
    if token:
        try:
            signed = TimestampSigner(salt="repurposer.media").unsign(
                token,
                max_age=24 * 60 * 60,
            ) == relative_path
        except (BadSignature, SignatureExpired):
            signed = False

    if not signed:
        owner_id = None
        parts = relative_path.split("/")
        if len(parts) >= 3 and parts[0] == "processed":
            try:
                from repurposer.models import Video
                owner_id = Video.objects.filter(pk=int(parts[2])).values_list(
                    "user_id", flat=True
                ).first()
            except (ValueError, TypeError):
                owner_id = None
        elif len(parts) >= 2 and parts[0] == "videos":
            from repurposer.models import Video
            owner_id = Video.objects.filter(
                video_file=relative_path
            ).values_list("user_id", flat=True).first()
        elif len(parts) >= 2 and parts[0] == "background_music":
            from repurposer.models import BackgroundMusic
            music = BackgroundMusic.objects.filter(
                audio_file=relative_path
            ).values("user_id", "is_default", "is_shared").first()
            if music:
                owner_id = music["user_id"]
                if music["is_shared"] or (owner_id is None and music["is_default"]):
                    owner_id = "global"
        elif len(parts) >= 2 and parts[0] == "profile_pictures":
            from repurposer.models import UserSettings
            owner_id = UserSettings.objects.filter(
                profile_picture=relative_path
            ).values_list("user_id", flat=True).first()
        elif len(parts) >= 2 and parts[0] == "advertisements":
            from repurposer.models import Advertisement
            if Advertisement.objects.filter(media_file=relative_path, is_active=True).exists():
                owner_id = "global"
        elif len(parts) >= 2 and parts[0] == "notifications" and request.user.is_authenticated:
            from repurposer.models import AdminNotification, NotificationRecipient
            notification_id = AdminNotification.objects.filter(image=relative_path).values_list("id", flat=True).first()
            if notification_id and NotificationRecipient.objects.filter(
                notification_id=notification_id,
                user=request.user,
                notification__is_active=True,
                notification__scheduled_at__lte=timezone.now(),
            ).exists():
                owner_id = request.user.id

        if not request.user.is_authenticated or not owner_id or (
            owner_id != "global" and owner_id != request.user.id
        ):
            return HttpResponseForbidden("Media access denied.")

    file_size = os.path.getsize(
        requested_path
    )

    modified_timestamp = os.path.getmtime(
        requested_path
    )

    etag = build_file_etag(
        file_size=file_size,
        modified_timestamp=modified_timestamp,
    )

    content_type, _ = mimetypes.guess_type(
        requested_path
    )

    if not content_type:
        content_type = (
            "application/octet-stream"
        )

    filename = os.path.basename(
        requested_path
    )

    # --------------------------------
    # HEAD request
    # --------------------------------

    if request.method == "HEAD":

        response = HttpResponse(
            status=200
        )

        response["Content-Type"] = (
            content_type
        )

        response["Content-Length"] = str(
            file_size
        )

        response["Accept-Ranges"] = "bytes"

        response["Cache-Control"] = "private, no-store"

        response["ETag"] = (
            f'"{etag}"'
        )

        response["Last-Modified"] = (
            formatdate(
                modified_timestamp,
                usegmt=True,
            )
        )

        response[
            "Content-Disposition"
        ] = (
            f'inline; filename="{filename}"'
        )

        response[
            "X-Content-Type-Options"
        ] = "nosniff"

        return response

    # --------------------------------
    # If client sent If-None-Match
    # --------------------------------

    client_etag = request.headers.get(
        "If-None-Match"
    )

    if client_etag:

        normalized_client_etag = (
            client_etag.strip()
        )

        normalized_server_etag = (
            f'"{etag}"'
        )

        if (
            normalized_client_etag
            == normalized_server_etag
        ):

            response = HttpResponse(
                status=304
            )

            response["ETag"] = (
                normalized_server_etag
            )

            response["Accept-Ranges"] = (
                "bytes"
            )

            response["Cache-Control"] = "private, no-store"

            return response

    # --------------------------------
    # No Range header
    # --------------------------------

    range_header = request.headers.get(
        "Range"
    )

    if not range_header:

        def file_chunks(start=0, length=None, chunk_size=64 * 1024):
            remaining = length
            with open(requested_path, "rb") as media_file:
                media_file.seek(start)
                while remaining is None or remaining > 0:
                    read_size = chunk_size if remaining is None else min(chunk_size, remaining)
                    chunk = media_file.read(read_size)
                    if not chunk:
                        break
                    if remaining is not None:
                        remaining -= len(chunk)
                    yield chunk

        response = StreamingHttpResponse(
            file_chunks(),
            status=200,
            content_type=content_type,
        )

        response["Content-Length"] = str(
            file_size
        )

        response["Accept-Ranges"] = (
            "bytes"
        )

        response["Cache-Control"] = "private, no-store"

        response["ETag"] = (
            f'"{etag}"'
        )

        response["Last-Modified"] = (
            formatdate(
                modified_timestamp,
                usegmt=True,
            )
        )

        response[
            "Content-Disposition"
        ] = (
            f'inline; filename="{filename}"'
        )

        response[
            "X-Content-Type-Options"
        ] = "nosniff"

        return response

    # --------------------------------
    # Validate Range header
    # --------------------------------

    if not range_header.startswith(
        "bytes="
    ):

        response = HttpResponse(
            status=416
        )

        response["Content-Range"] = (
            f"bytes */{file_size}"
        )

        response["Accept-Ranges"] = (
            "bytes"
        )

        return response

    range_value = range_header[
        len("bytes="):
    ].strip()

    # Only one range is supported.
    if "," in range_value:

        response = HttpResponse(
            status=416
        )

        response["Content-Range"] = (
            f"bytes */{file_size}"
        )

        response["Accept-Ranges"] = (
            "bytes"
        )

        return response

    # --------------------------------
    # Parse Range
    # --------------------------------

    try:

        start_text, end_text = (
            range_value.split(
                "-",
                1,
            )
        )

        if start_text == "":

            # Example:
            # bytes=-500

            suffix_length = int(
                end_text
            )

            if suffix_length <= 0:
                raise ValueError

            if suffix_length > file_size:
                suffix_length = file_size

            start = (
                file_size
                - suffix_length
            )

            end = file_size - 1

        else:

            start = int(
                start_text
            )

            if end_text == "":
                end = file_size - 1

            else:
                end = int(
                    end_text
                )

    except (
        ValueError,
        TypeError,
    ):

        response = HttpResponse(
            status=416
        )

        response["Content-Range"] = (
            f"bytes */{file_size}"
        )

        response["Accept-Ranges"] = (
            "bytes"
        )

        return response

    # --------------------------------
    # Validate range values
    # --------------------------------

    if (
        start < 0
        or start >= file_size
        or end < start
    ):

        response = HttpResponse(
            status=416
        )

        response["Content-Range"] = (
            f"bytes */{file_size}"
        )

        response["Accept-Ranges"] = (
            "bytes"
        )

        return response

    if end >= file_size:
        end = file_size - 1

    content_length = (
        end - start + 1
    )

    # --------------------------------
    # Read requested range
    # --------------------------------

    def range_chunks(chunk_size=64 * 1024):
        remaining = content_length
        with open(requested_path, "rb") as media_file:
            media_file.seek(start)
            while remaining > 0:
                chunk = media_file.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    # --------------------------------
    # Partial response
    # --------------------------------

    response = StreamingHttpResponse(
        range_chunks(),
        status=206,
        content_type=content_type,
    )

    response["Content-Length"] = str(
        content_length
    )

    response["Content-Range"] = (
        f"bytes {start}-{end}/{file_size}"
    )

    response["Accept-Ranges"] = (
        "bytes"
    )

    response["Cache-Control"] = "private, no-store"

    response["ETag"] = (
        f'"{etag}"'
    )

    response["Last-Modified"] = (
        formatdate(
            modified_timestamp,
            usegmt=True,
        )
    )

    response[
        "Content-Disposition"
    ] = (
        f'inline; filename="{filename}"'
    )

    response[
        "X-Content-Type-Options"
    ] = "nosniff"

    return response


urlpatterns = [
    # Authentication
    path(
        "register/",
        auth_views.register_view,
        name="register",
    ),
    path(
        "login/",
        auth_views.login_view,
        name="login",
    ),
    path(
        "logout/",
        auth_views.logout_view,
        name="logout",
    ),

    # Main application
    path(
        "",
        include("repurposer.urls"),
    ),

    # Keep the built-in Django admin login and model pages available after
    # custom application admin routes (including the dashboard at /admin/).
    path(
        "admin/",
        admin.site.urls,
    ),

    # Range-enabled media server
    path(
        "media/<path:file_path>",
        serve_media_file,
        name="serve_media_file",
    ),
]
