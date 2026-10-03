"""
YouTube trend intelligence service for AI Video Repurposer.

This module reads public YouTube "mostPopular" videos for a selected region.

Current implementation:
- YouTube Data API v3
- India region by default
- Most popular videos
- Public video title/channel/category/statistics
- No publishing
- No scheduling
- No modification of existing YouTube analytics logic
- No fake/placeholder metrics
"""

from __future__ import annotations

from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


YOUTUBE_API_VERSION = "v3"

# India
DEFAULT_REGION_CODE = "IN"

# Maximum videos requested from YouTube in one call.
DEFAULT_MAX_RESULTS = 25


def _build_youtube_service(api_key: str):
    """
    Create a YouTube Data API service using the project API key.
    """

    if not api_key:
        raise ValueError(
            "YouTube Data API key is required for trend intelligence."
        )

    return build(
        "youtube",
        YOUTUBE_API_VERSION,
        developerKey=api_key,
        cache_discovery=False,
    )


def _clean_video_item(item: dict[str, Any]) -> dict[str, Any]:
    """
    Convert the raw YouTube API video item into a small
    dashboard-friendly dictionary.
    """

    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})

    video_id = item.get("id", "")

    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "channel_id": snippet.get("channelId", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "published_at": snippet.get("publishedAt", ""),
        "description": snippet.get("description", ""),
        "category_id": snippet.get("categoryId", ""),
        "tags": snippet.get("tags", []),
        "thumbnail": (
            snippet.get("thumbnails", {})
            .get("high", {})
            .get("url", "")
        ),
        "views": int(
            statistics.get("viewCount", 0) or 0
        ),
        "likes": int(
            statistics.get("likeCount", 0) or 0
        ),
        "comments": int(
            statistics.get("commentCount", 0) or 0
        ),
        "url": (
            f"https://www.youtube.com/watch?v={video_id}"
            if video_id
            else ""
        ),
    }


def get_youtube_most_popular_videos(
    api_key: str,
    region_code: str = DEFAULT_REGION_CODE,
    max_results: int = DEFAULT_MAX_RESULTS,
    category_id: str | None = None,
) -> dict[str, Any]:
    """
    Fetch public most-popular YouTube videos for a region.

    Example:
        region_code="IN"

    Optional category_id can restrict the chart to a
    specific YouTube video category.
    """

    if not region_code:
        raise ValueError(
            "region_code is required."
        )

    if not 1 <= max_results <= 50:
        raise ValueError(
            "max_results must be between 1 and 50."
        )

    try:
        youtube = _build_youtube_service(api_key)

        request_params = {
            "part": "snippet,statistics",
            "chart": "mostPopular",
            "regionCode": region_code.upper(),
            "maxResults": max_results,
        }

        if category_id:
            request_params["videoCategoryId"] = str(
                category_id
            )

        response = (
            youtube.videos()
            .list(**request_params)
            .execute()
        )

        items = response.get(
            "items",
            [],
        )

        videos = [
            _clean_video_item(item)
            for item in items
        ]

        return {
            "available": True,
            "status": "live",
            "error": "",
            "region_code": region_code.upper(),
            "category_id": (
                str(category_id)
                if category_id
                else ""
            ),
            "count": len(videos),
            "videos": videos,
        }

    except HttpError as exc:
        status_code = getattr(
            exc.resp,
            "status",
            None,
        )

        return {
            "available": False,
            "status": "api_error",
            "http_status": status_code,
            "error": (
                "YouTube Data API rejected the trend request. "
                "Check the API key, YouTube Data API enablement, "
                "quota, and request parameters."
            ),
            "region_code": region_code.upper(),
            "videos": [],
        }

    except Exception as exc:
        return {
            "available": False,
            "status": "api_error",
            "error": str(exc),
            "region_code": region_code.upper(),
            "videos": [],
        }


def get_youtube_trend_context(
    api_key: str,
    region_code: str = DEFAULT_REGION_CODE,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """
    Return a compact context object intended for Gemini.

    This function does not call Gemini.
    It only collects verified public YouTube data.
    """

    result = get_youtube_most_popular_videos(
        api_key=api_key,
        region_code=region_code,
        max_results=max_results,
    )

    if not result.get("available"):
        return result

    videos = result.get(
        "videos",
        [],
    )

    trend_context = []

    for index, video in enumerate(
        videos,
        start=1,
    ):
        trend_context.append(
            {
                "rank": index,
                "video_id": video.get(
                    "video_id",
                    "",
                ),
                "title": video.get(
                    "title",
                    "",
                ),
                "channel_title": video.get(
                    "channel_title",
                    "",
                ),
                "category_id": video.get(
                    "category_id",
                    "",
                ),
                "published_at": video.get(
                    "published_at",
                    "",
                ),
                "views": video.get(
                    "views",
                    0,
                ),
                "likes": video.get(
                    "likes",
                    0,
                ),
                "comments": video.get(
                    "comments",
                    0,
                ),
                "url": video.get(
                    "url",
                    "",
                ),
            }
        )

    return {
        "available": True,
        "status": "live",
        "error": "",
        "region_code": result.get(
            "region_code",
            region_code.upper(),
        ),
        "count": len(trend_context),
        "videos": trend_context,
    }