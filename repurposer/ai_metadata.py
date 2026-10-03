import json
import time

from django.conf import settings
from google import genai


GEMINI_MODEL = "gemini-3.8-flash"
MAX_UPLOAD_RETRIES = 3
FILE_PROCESS_TIMEOUT_SECONDS = 180
FILE_POLL_INTERVAL_SECONDS = 3


def _get_client():
    api_key = getattr(settings, "GEMINI_API_KEY", "")

    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured.")

    return genai.Client(api_key=api_key)


def _parse_metadata(response_text, max_hashtags):
    response_text = (response_text or "").strip()

    if not response_text:
        raise ValueError("Gemini returned an empty response.")

    # Remove accidental markdown code fences.
    if response_text.startswith("```"):
        response_text = response_text.replace("```json", "", 1)
        response_text = response_text.replace("```", "", 1).strip()

    try:
        metadata = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Gemini returned invalid JSON: {response_text}"
        ) from exc

    title = str(metadata.get("title", "")).strip()
    description = str(metadata.get("description", "")).strip()
    hashtags = metadata.get("hashtags", [])

    if not title:
        raise ValueError("Gemini did not return a title.")

    if not description:
        raise ValueError("Gemini did not return a description.")

    if not isinstance(hashtags, list):
        hashtags = []

    hashtags = [
        str(tag).strip()
        for tag in hashtags
        if str(tag).strip()
    ]

    return {
        "title": title,
        "description": description,
        "hashtags": hashtags[:max_hashtags],
    }


def generate_video_metadata(clip_text):
    """
    Generate an AI title, description and hashtags
    for a video clip using Gemini.
    """

    client = _get_client()

    prompt = f"""
You are a professional social media content writer.

Create metadata for a short-form video clip.

Video/clip context:
{clip_text}

Return ONLY valid JSON in this exact format:

{{
    "title": "Short catchy title",
    "description": "Engaging 2-4 line description",
    "hashtags": ["#hashtag1", "#hashtag2", "#hashtag3", "#hashtag4", "#hashtag5"]
}}

Rules:
- Title should be short, catchy and natural.
- Do not use clickbait that makes false claims.
- Description should be 2-4 short lines.
- Hashtags must be relevant to the video.
- Use 5 hashtags.
- Do not add markdown.
- Do not add any explanation outside JSON.
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    return _parse_metadata(response.text, max_hashtags=5)


def _upload_video_with_retry(client, video_path):
    last_error = None

    for attempt in range(1, MAX_UPLOAD_RETRIES + 1):
        try:
            print(
                f"Uploading video to Gemini "
                f"(attempt {attempt}/{MAX_UPLOAD_RETRIES})..."
            )
            return client.files.upload(file=video_path)

        except Exception as exc:
            last_error = exc
            print(f"Gemini video upload attempt {attempt} failed: {exc}")

            if attempt < MAX_UPLOAD_RETRIES:
                time.sleep(3 * attempt)

    raise RuntimeError(
        "Gemini video upload failed after "
        f"{MAX_UPLOAD_RETRIES} attempts."
    ) from last_error


def _wait_for_file_active(client, uploaded_file):
    """
    Wait until Gemini finishes processing the uploaded video.
    """

    file_name = uploaded_file.name
    deadline = time.time() + FILE_PROCESS_TIMEOUT_SECONDS

    while time.time() < deadline:
        current_file = client.files.get(name=file_name)
        state = getattr(
            getattr(current_file, "state", None),
            "name",
            "",
        )

        print(f"Gemini video file state: {state or 'UNKNOWN'}")

        if state == "ACTIVE":
            return current_file

        if state in {"FAILED", "ERROR"}:
            raise RuntimeError(
                f"Gemini failed to process the video. "
                f"File state: {state}"
            )

        time.sleep(FILE_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        "Gemini video processing did not become ACTIVE within "
        f"{FILE_PROCESS_TIMEOUT_SECONDS} seconds."
    )


def _generate_video_content_with_retry(client, uploaded_file, prompt):
    last_error = None

    for attempt in range(1, MAX_UPLOAD_RETRIES + 1):
        try:
            print(
                f"Generating video metadata "
                f"(attempt {attempt}/{MAX_UPLOAD_RETRIES})..."
            )

            return client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    uploaded_file,
                    prompt,
                ],
            )

        except Exception as exc:
            last_error = exc
            print(
                f"Gemini metadata generation attempt "
                f"{attempt} failed: {exc}"
            )

            if attempt < MAX_UPLOAD_RETRIES:
                time.sleep(3 * attempt)

    raise RuntimeError(
        "Gemini metadata generation failed after "
        f"{MAX_UPLOAD_RETRIES} attempts."
    ) from last_error


def generate_video_file_metadata(video_path):
    """
    Generate title, description and 12 relevant hashtags
    directly from an actual video file.

    The uploaded Gemini file is allowed to finish processing
    before metadata generation starts.
    """

    client = _get_client()
    uploaded_file = None

    prompt = """
Analyze this short-form video carefully and understand its actual content.

Identify:
- Main subject/topic
- Specific scene or event
- Important people/characters if clearly identifiable from the content
- Objects, technology, locations or activities shown
- Visible text
- Genre/category
- Target audience
- Important keywords
- Context of the clip

Create high-quality social media metadata based ONLY on what is actually present
in the video. Do not invent facts.

Return ONLY valid JSON in this exact format:

{
    "title": "Short catchy title",
    "description": "Engaging description",
    "hashtags": [
        "#hashtag1",
        "#hashtag2",
        "#hashtag3",
        "#hashtag4",
        "#hashtag5",
        "#hashtag6",
        "#hashtag7",
        "#hashtag8",
        "#hashtag9",
        "#hashtag10",
        "#hashtag11",
        "#hashtag12"
    ]
}

TITLE RULES:
- Keep the title short and attention-grabbing.
- Clearly represent the actual video.
- Do not use fake claims.
- Do not use excessive emojis.
- Avoid misleading clickbait.
- Make it suitable for YouTube Shorts, Instagram Reels and Facebook Reels.

DESCRIPTION RULES:
- Write 2-4 natural lines.
- Explain what the viewer is actually seeing.
- Make it engaging but factual.
- Include important keywords naturally.
- Do not stuff keywords.
- Do not invent names, events or facts.

HASHTAG RULES:
- Generate exactly 12 highly relevant hashtags.
- Hashtags must be directly connected to the actual video.
- Give priority to specific topic hashtags over generic hashtags.
- Mix hashtags intelligently:
  1. Main topic
  2. Specific subject
  3. Scene/category
  4. Niche
  5. Audience
  6. Content format
- Avoid meaningless hashtag stuffing.
- Avoid unrelated #viral, #fyp, #trending tags unless the video genuinely relates to them.
- Do not repeat similar hashtags.
- Use proper readable hashtags.
- Every hashtag must have a clear reason to be included.

IMPORTANT:
The hashtags are more important than simply generating popular tags.
Choose hashtags that accurately describe what people would search for
when looking for this exact type of content.

Do not add markdown.
Do not add any explanation outside the JSON.
"""

    try:
        uploaded_file = _upload_video_with_retry(client, video_path)

        print(f"Gemini uploaded file: {uploaded_file.name}")

        uploaded_file = _wait_for_file_active(
            client,
            uploaded_file,
        )

        response = _generate_video_content_with_retry(
            client,
            uploaded_file,
            prompt,
        )

        return _parse_metadata(
            response.text,
            max_hashtags=12,
        )

    finally:
        # Clean up the temporary Gemini file when possible.
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
                print("Gemini temporary video file deleted.")
            except Exception as exc:
                print(
                    f"Gemini temporary file cleanup skipped: {exc}"
                )
