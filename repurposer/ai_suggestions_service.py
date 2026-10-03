import json
import time

from django.conf import settings
from google import genai


GEMINI_MODEL = "gemini-3.7-flash"
GEMINI_AI_ENABLED = False
MAX_SUGGESTIONS = 5
MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]


TEMPORARY_ERROR_MARKERS = (
    "503",
    "service unavailable",
    "temporarily unavailable",
    "unavailable",
    "overloaded",
    "high demand",
    "internal server error",
    "deadline exceeded",
    "timeout",
)


def _get_client():
    api_key = getattr(settings, "GEMINI_API_KEY", "")

    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured.")

    return genai.Client(api_key=api_key)


def _is_temporary_error(exc):
    error_text = str(exc).lower()
    return any(
        marker in error_text
        for marker in TEMPORARY_ERROR_MARKERS
    )


def _parse_suggestions(response_text):
    response_text = (response_text or "").strip()

    if not response_text:
        raise ValueError("Gemini returned an empty response.")

    if response_text.startswith("```"):
        response_text = response_text.replace("```json", "", 1)
        response_text = response_text.replace("```", "", 1).strip()

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Gemini returned invalid JSON: {response_text}"
        ) from exc

    suggestions = data.get("suggestions", [])

    if not isinstance(suggestions, list):
        raise ValueError("Gemini suggestions response is not a list.")

    cleaned = []

    for suggestion in suggestions[:MAX_SUGGESTIONS]:
        if not isinstance(suggestion, dict):
            continue

        category = str(
            suggestion.get("category", "")
        ).strip()
        title = str(
            suggestion.get("title", "")
        ).strip()
        insight = str(
            suggestion.get("insight", "")
        ).strip()
        action = str(
            suggestion.get("action", "")
        ).strip()

        if not title or not insight or not action:
            continue

        cleaned.append(
            {
                "category": category,
                "title": title,
                "insight": insight,
                "action": action,
            }
        )

    return {
        "suggestions": cleaned,
    }


def _build_trend_context(dashboard_context):
    """
    Keep only the verified public YouTube trend fields needed by Gemini.
    This prevents unnecessary API response data from being sent to Gemini.
    """

    trend_data = dashboard_context.get(
        "youtube_trends",
        {},
    )

    if not isinstance(trend_data, dict):
        return {
            "available": False,
            "status": "invalid_context",
            "videos": [],
        }

    if not trend_data.get("available"):
        return {
            "available": False,
            "status": trend_data.get(
                "status",
                "unavailable",
            ),
            "error": trend_data.get(
                "error",
                "",
            ),
            "region_code": trend_data.get(
                "region_code",
                "IN",
            ),
            "videos": [],
        }

    videos = []

    for video in trend_data.get("videos", []):
        if not isinstance(video, dict):
            continue

        videos.append(
            {
                "rank": video.get("rank"),
                "title": video.get("title", ""),
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
                "views": video.get("views", 0),
                "likes": video.get("likes", 0),
                "comments": video.get("comments", 0),
                "url": video.get("url", ""),
            }
        )

    return {
        "available": True,
        "status": trend_data.get(
            "status",
            "live",
        ),
        "region_code": trend_data.get(
            "region_code",
            "IN",
        ),
        "count": len(videos),
        "videos": videos,
    }


def _build_prompt(dashboard_context):
    trend_context = _build_trend_context(
        dashboard_context
    )

    context_for_gemini = dict(dashboard_context)
    context_for_gemini["youtube_trends"] = trend_context

    verified_context = json.dumps(
        context_for_gemini,
        ensure_ascii=False,
        indent=2,
        default=str,
    )

    return f"""
You are the AI strategy assistant inside a social media video repurposing dashboard.

Use ONLY the verified dashboard data provided below.

Your job is to generate practical, concise suggestions for the dashboard owner.

The dashboard now includes a live YouTube trend source when available.
When YouTube trend data is available, use it as a real source of current public
India YouTube popularity signals. Do not invent trends, rankings, view counts,
channels, or metrics.

IMPORTANT:
- Trend data is descriptive source data, not proof that a topic will perform well.
- Do not claim that a trend guarantees views, virality, growth, or success.
- Do not fabricate missing audience analytics.
- If dashboard publishing data is zero or missing, do not pretend there is a
  publishing history.
- Suggestions may be strategic recommendations, but clearly base them on the
  verified evidence available in the context.
- If trend data is unavailable, do not invent trend information.
- Prefer concrete suggestions that can actually be acted on inside a video
  repurposing/content workflow.
- Do not mention internal implementation details unless useful.

Create up to 5 suggestions.

Return ONLY valid JSON in exactly this format:

{{
  "suggestions": [
    {{
      "category": "Publishing | Engagement | Optimization | Analytics | Trends",
      "title": "Short suggestion title",
      "insight": "A concise explanation grounded in the verified data.",
      "action": "A concrete next action."
    }}
  ]
}}

Suggestion guidance:
1. Use YouTube trend data when available to identify observable content/topic
   patterns or opportunities.
2. Use the dashboard's own publishing and processing records when available.
3. Clearly separate observed data from strategic advice.
4. Do not repeat the same idea in multiple suggestions.
5. Keep each insight and action concise and useful.
6. If there is not enough evidence for a specific recommendation, give a safe,
   general workflow suggestion rather than inventing facts.

VERIFIED DASHBOARD CONTEXT:
{verified_context}

Do not add markdown.
Do not add explanations outside the JSON.
"""


def generate_dashboard_ai_suggestions(dashboard_context):
    """
    Generate dashboard AI suggestions from verified dashboard data.

    YouTube trend intelligence is included in the context when available.
    This function does not publish, schedule, upload, or modify any records.
    """

    if not GEMINI_AI_ENABLED:
        return {"suggestions": []}

    client = _get_client()
    prompt = _build_prompt(dashboard_context)
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )

            return _parse_suggestions(
                response.text
            )

        except Exception as exc:
            last_error = exc

            print(
                "Gemini dashboard suggestions attempt "
                f"{attempt}/{MAX_RETRIES} failed: {exc}"
            )

            if (
                attempt >= MAX_RETRIES
                or not _is_temporary_error(exc)
            ):
                raise

            time.sleep(
                RETRY_DELAYS[attempt - 1]
            )

    raise RuntimeError(
        "Gemini dashboard suggestions failed after "
        f"{MAX_RETRIES} attempts."
    ) from last_error




