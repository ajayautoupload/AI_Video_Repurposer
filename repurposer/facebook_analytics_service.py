"""
Facebook Page analytics service.

This module reads real Facebook Page data from the
Meta Graph API.

Current implementation:
- Facebook Page basic information
- Followers count when returned by the API
- 30-day Page Insights
- Real API values only
- No placeholder/fake analytics
- No publishing
- No scheduling

The Insights request intentionally starts with a single
metric so that Meta's current API response can be verified
before adding more metrics.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import requests


FACEBOOK_GRAPH_VERSION = "v23.0"


# ============================================================
# LOAD STORED FACEBOOK CREDENTIALS
# ============================================================

def _load_facebook_credentials(
    token_json: str,
) -> dict[str, Any]:
    """
    Load the stored Facebook credential JSON.
    """

    if not token_json:
        raise ValueError(
            "Facebook credential token is empty."
        )

    try:
        data = json.loads(
            token_json
        )

    except (
        TypeError,
        json.JSONDecodeError,
    ) as exc:

        raise ValueError(
            "Stored Facebook token_json "
            "is not valid JSON."
        ) from exc

    access_token = data.get(
        "access_token"
    )

    page_id = data.get(
        "page_id"
    )

    if not access_token:
        raise ValueError(
            "Facebook Page access token "
            "is missing."
        )

    if not page_id:
        raise ValueError(
            "Facebook Page ID is missing."
        )

    return data


# ============================================================
# GRAPH API GET
# ============================================================

def _graph_get(
    endpoint: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """
    Send a GET request to the Meta Graph API.
    """

    url = (
        f"https://graph.facebook.com/"
        f"{FACEBOOK_GRAPH_VERSION}/"
        f"{endpoint}"
    )

    response = requests.get(
        url,
        params=params,
        timeout=30,
    )

    try:
        data = response.json()

    except ValueError:

        data = {
            "raw_response": response.text
        }

    if not response.ok:

        error = data.get(
            "error",
            {}
        )

        if isinstance(
            error,
            dict,
        ):

            error_message = (
                error.get(
                    "message"
                )
                or "Facebook Graph API request failed."
            )

            error_type = (
                error.get(
                    "type"
                )
            )

            error_code = (
                error.get(
                    "code"
                )
            )

            error_subcode = (
                error.get(
                    "error_subcode"
                )
            )

            raise ValueError(
                "Facebook Graph API request failed: "
                f"{error_message} | "
                f"type={error_type} | "
                f"code={error_code} | "
                f"subcode={error_subcode} | "
                f"HTTP {response.status_code}"
            )

        raise ValueError(
            "Facebook Graph API request failed: "
            f"{data} | "
            f"HTTP {response.status_code}"
        )

    return data


# ============================================================
# FACEBOOK PAGE ANALYTICS
# ============================================================

def get_facebook_page_analytics(
    token_json: str,
    days: int = 30,
) -> dict[str, Any]:
    """
    Fetch real Facebook Page analytics.

    Supported reporting periods:
        7 days
        30 days
        90 days

    No fake values are returned.
    """

    if days not in (
        7,
        30,
        90,
    ):

        raise ValueError(
            "days must be 7, 30, or 90."
        )

    # ========================================================
    # LOAD CREDENTIALS
    # ========================================================

    try:

        credentials = (
            _load_facebook_credentials(
                token_json
            )
        )

    except Exception as exc:

        return {
            "available": False,

            "status": "credential_error",

            "error": str(exc),
        }

    access_token = credentials.get(
        "access_token"
    )

    page_id = credentials.get(
        "page_id"
    )

    page_name = credentials.get(
        "page_name",
        "",
    )

    try:

        # ====================================================
        # PAGE BASIC INFORMATION
        # ====================================================

        page_data = _graph_get(
            page_id,
            {
                "fields": (
                    "id,"
                    "name,"
                    "followers_count"
                ),

                "access_token": (
                    access_token
                ),
            },
        )

        # ====================================================
        # DATE RANGE
        # ====================================================

        today = date.today()

        start_date = (
            today
            - timedelta(
                days=days
            )
        )

        # ====================================================
        # PAGE INSIGHTS
        # ====================================================
        #
        # IMPORTANT:
        # A specific metric is required.
        #
        # We intentionally begin with one metric.
        # Once Meta returns the real response successfully,
        # additional supported metrics can be added safely.
        #
        # ====================================================

        insights_data = _graph_get(
            f"{page_id}/insights",
            {
                "metric": (
                    "page_post_engagements"
                ),

                "period": "day",

                "since": (
                    start_date.isoformat()
                ),

                "until": (
                    today.isoformat()
                ),

                "access_token": (
                    access_token
                ),
            },
        )

        insights = (
            insights_data.get(
                "data",
                []
            )
        )

        # ====================================================
        # RETURN REAL API DATA
        # ====================================================

        return {
            "available": True,

            "status": "live",

            "error": "",

            "page_id": (
                page_data.get(
                    "id",
                    page_id,
                )
            ),

            "page_name": (
                page_data.get(
                    "name",
                    page_name,
                )
            ),

            "followers": (
                page_data.get(
                    "followers_count"
                )
            ),

            "period_days": days,

            "period_start": (
                start_date.isoformat()
            ),

            "period_end": (
                today.isoformat()
            ),

            "insights": insights,
        }

    except Exception as exc:

        return {
            "available": False,

            "status": "api_error",

            "error": str(exc),

            "page_id": page_id,

            "page_name": page_name,

            "period_days": days,
        }


# ============================================================
# FACEBOOK DASHBOARD ANALYTICS
# ============================================================

def get_facebook_dashboard_analytics(
    facebook_credentials,
) -> dict[str, Any]:
    """
    Get Facebook analytics for all active
    Facebook Page credentials.

    The dashboard always uses a 30-day period.
    """

    results = []

    for credential in facebook_credentials:

        analytics = (
            get_facebook_page_analytics(
                credential.token_json,
                days=30,
            )
        )

        results.append(
            {
                "credential_id": (
                    credential.id
                ),

                "account_name": (
                    getattr(
                        credential,
                        "page_name",
                        None,
                    )
                    or (
                        f"Facebook Page "
                        f"#{credential.id}"
                    )
                ),

                **analytics,
            }
        )

    return {
        "platform": "facebook",

        "available": any(
            item.get(
                "available"
            )
            for item in results
        ),

        "accounts": results,
    }