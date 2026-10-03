"""
Social analytics service for AI Video Repurposer.

This module intentionally returns only metrics verified by the platform API.
It never creates placeholder/fake follower, view, like, or comment numbers.

STEP 1 currently implements YouTube:
- Channel statistics from YouTube Data API
- 30-day channel analytics from YouTube Analytics API

Important:
Existing YouTube OAuth tokens in this project were originally created for
uploading. Detailed analytics requires the additional read/analytics scopes.
If the stored token does not contain those scopes, this service returns a
clear "reauthorization_required" state instead of showing fake data.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


YOUTUBE_READ_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _build_youtube_credentials(token_json: str) -> Credentials:
    """Build Google credentials from the token_json stored in the database."""
    if not token_json:
        raise ValueError("YouTube credential token is empty.")

    try:
        token_data = json.loads(token_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Stored YouTube token_json is not valid JSON.") from exc

    credentials = Credentials.from_authorized_user_info(token_data)

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    return credentials


def _has_required_scope(credentials: Credentials, required_scope: str) -> bool:
    """Check whether the stored OAuth token was granted a required scope."""
    granted_scopes = set(credentials.scopes or [])
    return required_scope in granted_scopes


def _scope_status(credentials: Credentials) -> dict[str, Any]:
    """Return an explicit status when the token needs reauthorization."""
    missing = [
        scope
        for scope in YOUTUBE_READ_SCOPES
        if not _has_required_scope(credentials, scope)
    ]

    if missing:
        return {
            "available": False,
            "status": "reauthorization_required",
            "missing_scopes": missing,
            "error": (
                "YouTube account must be connected again with "
                "analytics/read permissions before real analytics "
                "can be displayed."
            ),
        }

    return {
        "available": True,
        "status": "connected",
        "missing_scopes": [],
        "error": "",
    }


def get_youtube_channel_analytics(
    token_json: str,
    days: int = 30,
) -> dict[str, Any]:
    """
    Fetch verified YouTube channel statistics and analytics.

    Returned values:
        subscribers
        total_channel_views
        public_video_count
        period_views
        period_likes
        period_comments
        subscribers_gained
        subscribers_lost
        period_days

    No values are fabricated. When OAuth permissions are insufficient,
    available=False is returned.
    """
    if days not in (7, 30, 90):
        raise ValueError("days must be 7, 30, or 90.")

    try:
        credentials = _build_youtube_credentials(token_json)
    except Exception as exc:
        return {
            "available": False,
            "status": "credential_error",
            "error": str(exc),
        }

    scope_status = _scope_status(credentials)

    if not scope_status["available"]:
        return scope_status

    try:
        youtube = build(
            "youtube",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )

        channel_response = (
            youtube.channels()
            .list(
                part="snippet,statistics,contentDetails",
                mine=True,
            )
            .execute()
        )

        items = channel_response.get("items", [])

        if not items:
            return {
                "available": False,
                "status": "channel_not_found",
                "error": "No YouTube channel was returned for this account.",
            }

        channel = items[0]
        statistics = channel.get("statistics", {})
        snippet = channel.get("snippet", {})

        today = date.today()
        start_date = today - timedelta(days=days)

        analytics = build(
            "youtubeAnalytics",
            "v2",
            credentials=credentials,
            cache_discovery=False,
        )

        report = (
            analytics.reports()
            .query(
                ids="channel==MINE",
                startDate=start_date.isoformat(),
                endDate=today.isoformat(),
                metrics=(
                    "views,likes,comments,"
                    "subscribersGained,subscribersLost"
                ),
            )
            .execute()
        )

        rows = report.get("rows", [])

        period_views = 0
        period_likes = 0
        period_comments = 0
        subscribers_gained = 0
        subscribers_lost = 0

        for row in rows:
            period_views += int(row[0] or 0)
            period_likes += int(row[1] or 0)
            period_comments += int(row[2] or 0)
            subscribers_gained += int(row[3] or 0)
            subscribers_lost += int(row[4] or 0)

        return {
            "available": True,
            "status": "live",
            "error": "",
            "channel_id": channel.get("id", ""),
            "channel_title": snippet.get("title", ""),
            "subscribers": int(statistics.get("subscriberCount", 0)),
            "total_channel_views": int(statistics.get("viewCount", 0)),
            "public_video_count": int(statistics.get("videoCount", 0)),
            "period_days": days,
            "period_views": period_views,
            "period_likes": period_likes,
            "period_comments": period_comments,
            "subscribers_gained": subscribers_gained,
            "subscribers_lost": subscribers_lost,
            "net_subscribers": subscribers_gained - subscribers_lost,
        }

    except HttpError as exc:
        status_code = getattr(exc.resp, "status", None)

        return {
            "available": False,
            "status": "api_error",
            "http_status": status_code,
            "error": (
                "YouTube API rejected the analytics request. "
                "Check OAuth scopes, API enablement, and account access."
            ),
        }

    except Exception as exc:
        return {
            "available": False,
            "status": "api_error",
            "error": str(exc),
        }


def get_youtube_dashboard_analytics(
    youtube_credentials,
) -> dict[str, Any]:
    """
    Build dashboard-ready analytics for all active YouTube credentials.

    The database model is intentionally not modified in this step.
    """
    results = []

    for credential in youtube_credentials:
        analytics = get_youtube_channel_analytics(
            credential.token_json,
            days=30,
        )

        results.append(
            {
                "credential_id": credential.id,
                "account_name": (
                    getattr(credential, "channel_name", None)
                    or getattr(credential, "title", None)
                    or getattr(credential, "channel_title", None)
                    or f"YouTube Account #{credential.id}"
                ),
                **analytics,
            }
        )

    return {
        "platform": "youtube",
        "available": any(item["available"] for item in results),
        "accounts": results,
    }
