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
    HttpResponseNotFound,
)
from django.urls import include, path 
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

    response["Cache-Control"] = (
        "public, max-age=3600, no-transform"
    )

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

    Important:
    Video responses are intentionally returned as normal
    HttpResponse objects instead of StreamingHttpResponse.

    This makes the response more predictable for external
    media fetchers such as Instagram.
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

    media_root = os.path.abspath(
        str(settings.MEDIA_ROOT)
    )

    requested_path = os.path.abspath(
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

        response["Cache-Control"] = (
            "public, max-age=3600, no-transform"
        )

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

            response["Cache-Control"] = (
                "public, max-age=3600, no-transform"
            )

            return response

    # --------------------------------
    # No Range header
    # --------------------------------

    range_header = request.headers.get(
        "Range"
    )

    if not range_header:

        with open(
            requested_path,
            "rb",
        ) as media_file:

            file_data = media_file.read()

        response = HttpResponse(
            file_data,
            status=200,
            content_type=content_type,
        )

        response["Content-Length"] = str(
            file_size
        )

        response["Accept-Ranges"] = (
            "bytes"
        )

        response["Cache-Control"] = (
            "public, max-age=3600, no-transform"
        )

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

    with open(
        requested_path,
        "rb",
    ) as media_file:

        media_file.seek(
            start
        )

        file_data = media_file.read(
            content_length
        )

    # --------------------------------
    # Partial response
    # --------------------------------

    response = HttpResponse(
        file_data,
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

    response["Cache-Control"] = (
        "public, max-age=3600, no-transform"
    )

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
    path(
        "admin/",
        admin.site.urls,
    ),

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

    # Range-enabled media server
    path(
        "media/<path:file_path>",
        serve_media_file,
        name="serve_media_file",
    ),
]