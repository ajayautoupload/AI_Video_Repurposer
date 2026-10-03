from pathlib import Path
import json
import threading
from datetime import datetime

import requests

from django.shortcuts import (
    render,
    redirect,
    get_object_or_404,
)
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.http import (
    HttpResponse,
    JsonResponse,
)
from django.middleware.csrf import get_token
from django.utils import timezone

from .forms import VideoUploadForm

from .models import (
    Video,
    YouTubeCredential,
    InstagramCredential,
    FacebookCredential,
    ScheduledPost,
    XCredential,
)

from .processing_service import (
    process_video,
)

from .short_compositor import (
    create_vertical_short,
)

from .ai_metadata import (
    generate_video_file_metadata,
)
from .ai_suggestions_service import (
    generate_dashboard_ai_suggestions,
)
from .youtube_service import (
    upload_video_to_youtube,
    get_next_youtube_schedule_slots,
)

from .scheduling_service import (
    get_next_schedule_slots,
)

from .instagram_oauth import (
    get_instagram_authorization_url,
    exchange_instagram_code_for_token,
    exchange_short_lived_token_for_long_lived_token,
    get_instagram_account_info,
    save_instagram_credentials,
)

from .youtube_oauth import (
    get_youtube_authorization_url,
    exchange_code_for_credentials,
    get_youtube_channel_info,
    save_youtube_credentials,
)

from .youtube_analytics_service import (
    get_youtube_dashboard_analytics,
)

from .youtube_trends_service import (
    get_youtube_trend_context,
)

from .facebook_oauth import (
    create_facebook_oauth_state,
    get_facebook_authorization_url,
    get_facebook_pages,
)

from .facebook_service import (
    upload_scheduled_video_to_facebook,
)

from .x_oauth import (
    build_x_authorization_url,
    exchange_code_for_token,
    get_x_user,
    save_x_credential,
)

from .x_service import (
    publish_video_to_x,
)

from .instagram_service import (
    upload_reel_to_instagram,
)

from .facebook_service import (
    publish_video_to_facebook,
)


def check_public_video_url(video_url):
    """Verify the public clip URL before Instagram fetches it."""
    try:
        response = requests.get(
            video_url,
            stream=True,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise ValueError(
            "Instagram public video URL could not be reached. "
            "Check the Cloudflare tunnel and Django server."
        ) from exc

    try:
        if response.status_code != 200:
            raise ValueError(
                "Instagram public video URL returned HTTP "
                f"{response.status_code}."
            )

        content_type = (
            response.headers.get("Content-Type") or ""
        ).lower()

        if not content_type.startswith("video/"):
            raise ValueError(
                "Instagram public video URL did not return a video. "
                f"Content-Type: {content_type or 'missing'}"
            )
    finally:
        response.close()


def _configure_video_form_for_user(form, user):
    """Limit account choices in the upload form to the logged-in user."""

    if not user.is_authenticated:
        form.fields["selected_youtube_channel"].queryset = YouTubeCredential.objects.none()
        form.fields["selected_instagram_account"].queryset = InstagramCredential.objects.none()
        form.fields["selected_facebook_page"].queryset = FacebookCredential.objects.none()
        form.fields["selected_x_account"].queryset = XCredential.objects.none()
        return form

    form.fields["selected_youtube_channel"].queryset = YouTubeCredential.objects.filter(
        user=user,
        is_active=True,
    )
    form.fields["selected_instagram_account"].queryset = InstagramCredential.objects.filter(
        user=user,
        is_active=True,
    )
    form.fields["selected_facebook_page"].queryset = FacebookCredential.objects.filter(
        user=user,
        is_active=True,
    )
    form.fields["selected_x_account"].queryset = XCredential.objects.filter(
        owner=user,
        is_active=True,
    )

    return form


def _clip_map(video):
    return {
        str(clip["number"]): clip
        for clip in get_generated_clips(video)
    }


def _get_manual_accounts(request):
    """Resolve selected manual accounts independently per platform."""

    def get_active_account(model, field_name):
        account_id = request.POST.get(field_name, "").strip()

        # Empty or non-numeric account ID means no account selected.
        if not account_id or not account_id.isdigit():
            return None

        filters = {
            "id": int(account_id),
            "is_active": True,
        }

        if model is XCredential:
            filters["owner"] = request.user
        else:
            filters["user"] = request.user

        return model.objects.filter(
            **filters,
        ).first()

    return {
        "youtube": get_active_account(
            YouTubeCredential,
            "youtube_channel",
        ),
        "instagram": get_active_account(
            InstagramCredential,
            "instagram_account",
        ),
        "facebook": get_active_account(
            FacebookCredential,
            "facebook_page",
        ),
        "x": get_active_account(
            XCredential,
            "x_account",
        ),
    }

def _manual_selection(request, video):
    clip_numbers = request.POST.getlist("clip_numbers")
    platforms = request.POST.getlist("platforms")

    if not clip_numbers:
        raise ValueError("Select at least one clip.")

    allowed_platforms = {
        "youtube", "instagram", "facebook", "x"
    }
    platforms = [
        platform for platform in platforms
        if platform in allowed_platforms
    ]

    if not platforms:
        raise ValueError("Select at least one platform.")

    clips = _clip_map(video)
    selected_clips = []

    for number in clip_numbers:
        clip = clips.get(str(number))
        if clip:
            selected_clips.append(clip)

    if not selected_clips:
        raise ValueError("Selected clips are not available.")

    accounts = _get_manual_accounts(request)

    for platform in platforms:
        if not accounts.get(platform):
            raise ValueError(
                f"Select an active {platform.title()} account/page."
            )

    return selected_clips, platforms, accounts


def _relative_clip_path(clip):
    absolute_path = Path(clip["file_path"])
    relative_path = absolute_path.relative_to(settings.MEDIA_ROOT)
    return str(relative_path).replace("\\", "/")


def _publish_manual_clip(video, clip, platform, account):
    """Publish one clip to one selected platform."""
    title = f"AI Video Repurposer Clip {clip['number']}"
    description = "Published manually by AI Video Repurposer."
    video_path = clip["file_path"]

    if platform == "youtube":
        result = upload_video_to_youtube(
            video_path=video_path,
            token_json=account.token_json,
            title=title,
            description=description,
            tags=[
                "AI Video Repurposer",
                "Short Video",
                "Video Repurposing",
            ],
            privacy_status="public",
            publish_at=None,
        )
        return {
            "video_id": result.get("video_id"),
            "external_url": result.get("url", ""),
        }

    if platform == "instagram":
        token_data = json.loads(account.token_json)
        access_token = token_data.get("access_token")
        user_id = token_data.get("account_id")

        if not access_token:
            raise ValueError("Instagram access token is missing.")
        if not user_id:
            raise ValueError("Instagram account ID is missing.")

        public_url = (
            f"{settings.PUBLIC_BASE_URL.rstrip('/')}/"
            f"{clip['path'].lstrip('/')}"
        )
        check_public_video_url(public_url)

        result = upload_reel_to_instagram(
            instagram_user_id=user_id,
            access_token=access_token,
            video_url=public_url,
            caption=description,
            use_facebook_graph=False,

        )
        return {
            "external_post_id": result.get("media_id", ""),
            "external_url": public_url,
        }

    if platform == "facebook":
        token_data = json.loads(account.token_json)
        page_access_token = token_data.get("access_token")
        page_id = token_data.get("page_id")

        if not page_access_token:
            raise ValueError("Facebook Page access token is missing.")
        if not page_id:
            raise ValueError("Facebook Page ID is missing.")

        result = publish_video_to_facebook(
            page_id=page_id,
            page_access_token=page_access_token,
            video_path=video_path,
            title=title,
            description=description,
        )
        return {
            "external_post_id": result.get("video_id", ""),
        }

    if platform == "x":
        result = publish_video_to_x(
            x_credential=account,
            video_path=video_path,
            text=title,
        )
        post_id = result.get("post_id")
        external_url = ""
        if post_id and account.username:
            external_url = (
                f"https://x.com/{account.username}/status/{post_id}"
            )
        return {
            "external_post_id": post_id or "",
            "external_url": external_url,
        }

    raise ValueError(f"Unsupported platform: {platform}")


# ============================================================
# GENERATED CLIPS
# ============================================================


def get_generated_clips(video):
    """
    Rebuild generated clip information from files created by
    processing_service.py.
    """

    generated_clips = []

    if not video:

        return generated_clips

    # ========================================================
    # VIRAL CLIPS
    # ========================================================

    if video.processing_mode == 'viral':

        output_directory = (
            Path(settings.MEDIA_ROOT)
            / 'processed'
            / 'viral'
            / str(video.id)
        )

        if not output_directory.exists():

            return generated_clips

        clip_files = sorted(
            output_directory.glob(
             'normal_clip_*.mp4'

            ),
            key=lambda path: int(
                path.stem.split('_')[-1]
            ),
        )

        for index, clip_file in enumerate(
            clip_files,
            start=1,
        ):

            relative_path = (
                clip_file.relative_to(
                    settings.MEDIA_ROOT
                )
            )

            media_path = (
                str(relative_path)
                .replace('\\', '/')
            )

            generated_clips.append(
                {
                    'number': index,

                    'path': (
                        f'{settings.MEDIA_URL}'
                        f'{media_path}'
                    ),

                    'file_path': str(
                        clip_file
                    ),

                    'start': 0,

                    'end': 0,

                    'duration': 0,

                    'text': (
                        'Viral clip generated by AI.'
                    ),

                    'score': '—',
                }
            )

    # ========================================================
    # NORMAL CLIPS
    # ========================================================

    elif video.processing_mode == 'normal':

        output_directory = (
            Path(settings.MEDIA_ROOT)
            / 'processed'
            / 'normal'
            / str(video.id)
        )

        if not output_directory.exists():

            return generated_clips

        clip_files = sorted(
            output_directory.glob(
                'normal_clip_*.mp4'

            ),
            key=lambda path: int(
                path.stem.split('_')[-1]
            ),
        )

        for index, clip_file in enumerate(
            clip_files,
            start=1,
        ):

            relative_path = (
                clip_file.relative_to(
                    settings.MEDIA_ROOT
                )
            )

            media_path = (
                str(relative_path)
                .replace('\\', '/')
            )

            generated_clips.append(
                {
                    'number': index,

                    'path': (
                        f'{settings.MEDIA_URL}'
                        f'{media_path}'
                    ),

                    'file_path': str(
                        clip_file
                    ),

                    'start': 0,

                    'end': 0,

                    'duration': 0,

                    'text': (
                        'Normal sequential clip.'
                    ),

                    'score': '—',
                }
            )

    return generated_clips


# ============================================================
# AI METADATA
# ============================================================


def enrich_clips_with_ai_metadata(generated_clips):
    """
    Generate title, description and hashtags for each newly
    generated clip using Gemini.

    If Gemini fails for one clip, keep the clip usable with
    a safe fallback instead of stopping the entire workflow.
    """

    if not generated_clips:
        return generated_clips

    enriched_clips = []

    for clip in generated_clips:
        updated_clip = dict(clip)

        try:
            print(
                "Generating AI metadata for clip "
                f"{clip['number']}..."
            )

            metadata = generate_video_file_metadata(
                clip["file_path"]
            )

            updated_clip["title"] = metadata["title"]
            updated_clip["description"] = metadata["description"]
            updated_clip["hashtags"] = metadata["hashtags"]

            print(
                "AI metadata generated successfully for clip "
                f"{clip['number']}."
            )

        except Exception as exc:
            print(
                "AI metadata generation failed for clip "
                f"{clip['number']}: {exc}"
            )

            updated_clip["title"] = (
                f"AI Video Repurposer Clip {clip['number']}"
            )

            updated_clip["description"] = (
                "A short-form video created with "
                "AI Video Repurposer."
            )

            updated_clip["hashtags"] = [
                "#Shorts",
                "#Reels",
                "#AI",
                "#Video",
                "#VideoRepurposing",
            ]

        enriched_clips.append(updated_clip)

    return enriched_clips


# ============================================================
# AUTO SYSTEM
# ============================================================


def run_auto_publishing(
    video,
    generated_clips,
):
    """
    Auto System publishing/scheduling.

    IMPORTANT:
    A failure on one platform must never stop the other
    platforms.

    Each platform is isolated with its own exception handling.
    """

    youtube_upload_results = []

    instagram_publish_results = []

    facebook_publish_results = []

    x_publish_results = []

    platform_errors = []


    # ========================================================
    # YOUTUBE
    # ========================================================

    if (
        generated_clips
        and video.selected_youtube_channel
    ):

        selected_channel = (
            video.selected_youtube_channel
        )

        try:

            schedule_slots = (
                get_next_youtube_schedule_slots(
                    len(generated_clips)
                )
            )

        except Exception as exc:

            error_message = str(exc)

            platform_errors.append(
                {
                    'platform': 'youtube',
                    'clip_number': None,
                    'error': error_message,
                }
            )

            print(
                'YouTube schedule slot creation failed:'
            )

            print(
                error_message
            )

        else:

            for clip, publish_at in zip(
                generated_clips,
                schedule_slots,
            ):

                try:

                    print(
                        'Starting YouTube upload '
                        f'for clip {clip["number"]}...'
                    )

                    upload_result = (
                        upload_video_to_youtube(
                            video_path=(
                                clip['file_path']
                            ),

                            token_json=(
                                selected_channel.token_json
                            ),

                            title=(
                                clip.get(
                                    'title',
                                    f'AI Video Repurposer '
                                    f'Clip {clip["number"]}',
                                )
                            ),

                            description=(
                                clip.get(
                                    'description',
                                    'A short-form video created '
                                    'with AI Video Repurposer.',
                                )
                                + '\n\n'
                                + ' '.join(
                                    clip.get('hashtags', [])
                                )
                            ),

                            tags=[
                                tag.lstrip('#')
                                for tag in clip.get(
                                    'hashtags',
                                    [],
                                )
                            ],

                            privacy_status='private',

                            publish_at=publish_at,
                        )
                    )

                    result = {
                        'clip_number': (
                            clip['number']
                        ),

                        'video_id': (
                            upload_result[
                                'video_id'
                            ]
                        ),

                        'url': (
                            upload_result[
                                'url'
                            ]
                        ),

                        'publish_at': (
                            publish_at.isoformat()
                        ),

                        'privacy_status': (
                            upload_result[
                                'privacy_status'
                            ]
                        ),

                        'status': 'scheduled',

                        'error': '',
                    }

                    youtube_upload_results.append(
                        result
                    )

                    print(
                        'YouTube scheduled upload '
                        'successful:'
                    )

                    print(
                        result
                    )

                except Exception as exc:

                    error_message = str(
                        exc
                    )

                    result = {
                        'clip_number': (
                            clip['number']
                        ),

                        'video_id': None,

                        'url': '',

                        'publish_at': (
                            publish_at.isoformat()
                        ),

                        'privacy_status': '',

                        'status': 'failed',

                        'error': error_message,
                    }

                    youtube_upload_results.append(
                        result
                    )

                    platform_errors.append(
                        {
                            'platform': 'youtube',

                            'clip_number': (
                                clip['number']
                            ),

                            'error': error_message,
                        }
                    )

                    print(
                        'YouTube upload failed '
                        f'for clip {clip["number"]}:'
                    )

                    print(
                        error_message
                    )

                    print(
                        'Continuing with the next '
                        'YouTube clip...'
                    )


    # ========================================================
    # INSTAGRAM
    # ========================================================

    if (
        generated_clips
        and video.selected_instagram_account
    ):

        selected_instagram = (
            video.selected_instagram_account
        )

        try:

            schedule_slots = (
                get_next_schedule_slots(
                    len(generated_clips)
                )
            )

        except Exception as exc:

            error_message = str(
                exc
            )

            platform_errors.append(
                {
                    'platform': 'instagram',

                    'clip_number': None,

                    'error': error_message,
                }
            )

            print(
                'Instagram schedule slot creation failed:'
            )

            print(
                error_message
            )

        else:

            for clip, scheduled_at in zip(
                generated_clips,
                schedule_slots,
            ):

                try:

                    absolute_path = Path(
                        clip['file_path']
                    )

                    relative_path = (
                        absolute_path.relative_to(
                            settings.MEDIA_ROOT
                        )
                    )

                    video_path = (
                        str(relative_path)
                        .replace('\\', '/')
                    )

                    scheduled_post = (
                        ScheduledPost.objects.create(
                        user=video.user,
                            video=video,

                            video_path=video_path,

                            platform='instagram',

                            instagram_account=(
                                selected_instagram
                            ),

                            title=(
                                clip.get(
                                    'title',
                                    f'AI Video Repurposer '
                                    f'Clip {clip["number"]}',
                                )
                            ),

                            description=(
                                clip.get(
                                    'description',
                                    'A short-form video created '
                                    'with AI Video Repurposer.',
                                )
                                + '\n\n'
                                + ' '.join(
                                    clip.get('hashtags', [])
                                )
                            ),

                            scheduled_at=(
                                scheduled_at
                            ),

                            status='scheduled',
                        )
                    )

                    result = {
                        'clip_number': (
                            clip['number']
                        ),

                        'scheduled_post_id': (
                            scheduled_post.id
                        ),

                        'scheduled_at': (
                            scheduled_at.isoformat()
                        ),

                        'status': 'scheduled',

                        'error': '',
                    }

                    instagram_publish_results.append(
                        result
                    )

                    print(
                        'Instagram post scheduled:'
                    )

                    print(
                        result
                    )

                except Exception as exc:

                    error_message = str(
                        exc
                    )

                    result = {
                        'clip_number': (
                            clip['number']
                        ),

                        'scheduled_post_id': None,

                        'scheduled_at': (
                            scheduled_at.isoformat()
                        ),

                        'status': 'failed',

                        'error': error_message,
                    }

                    instagram_publish_results.append(
                        result
                    )

                    platform_errors.append(
                        {
                            'platform': 'instagram',

                            'clip_number': (
                                clip['number']
                            ),

                            'error': error_message,
                        }
                    )

                    print(
                        'Instagram scheduling failed '
                        f'for clip {clip["number"]}:'
                    )

                    print(
                        error_message
                    )

                    print(
                        'Continuing with the next '
                        'Instagram clip...'
                    )


    # ========================================================
    # FACEBOOK
    # ========================================================

    if (
        generated_clips
        and video.selected_facebook_page
    ):

        selected_facebook = (
            video.selected_facebook_page
        )

        try:

            facebook_token_data = json.loads(
                selected_facebook.token_json
            )

            facebook_page_access_token = (
                facebook_token_data.get(
                    'access_token'
                )
            )

            facebook_page_id = (
                facebook_token_data.get(
                    'page_id'
                )
            )

            if not facebook_page_access_token:

                raise ValueError(
                    'Facebook Page access token '
                    'is missing.'
                )

            if not facebook_page_id:

                raise ValueError(
                    'Facebook Page ID is missing.'
                )

            schedule_slots = (
                get_next_schedule_slots(
                    len(generated_clips)
                )
            )

        except Exception as exc:

            error_message = str(
                exc
            )

            platform_errors.append(
                {
                    'platform': 'facebook',

                    'clip_number': None,

                    'error': error_message,
                }
            )

            print(
                'Facebook scheduling setup failed:'
            )

            print(
                error_message
            )

        else:

            for clip, scheduled_at in zip(
                generated_clips,
                schedule_slots,
            ):

                try:

                    scheduled_publish_time = int(
                        scheduled_at.timestamp()
                    )

                    print(
                        'Starting Facebook scheduled '
                        f'upload for clip {clip["number"]}...'
                    )

                    facebook_result = (
                        upload_scheduled_video_to_facebook(
                            page_id=(
                                facebook_page_id
                            ),

                            page_access_token=(
                                facebook_page_access_token
                            ),

                            video_path=(
                                clip['file_path']
                            ),

                            scheduled_publish_time=(
                                scheduled_publish_time
                            ),

                            title=(
                                clip.get(
                                    'title',
                                    f'AI Video Repurposer '
                                    f'Clip {clip["number"]}',
                                )
                            ),

                            description=(
                                clip.get(
                                    'description',
                                    'A short-form video created '
                                    'with AI Video Repurposer.',
                                )
                                + '\n\n'
                                + ' '.join(
                                    clip.get('hashtags', [])
                                )
                            ),
                        )
                    )

                    absolute_path = Path(
                        clip['file_path']
                    )

                    relative_path = (
                        absolute_path.relative_to(
                            settings.MEDIA_ROOT
                        )
                    )

                    video_path = (
                        str(relative_path)
                        .replace('\\', '/')
                    )

                    scheduled_post = (
                        ScheduledPost.objects.create(
                        user=video.user,
                            video=video,

                            video_path=video_path,

                            platform='facebook',

                            facebook_page=(
                                selected_facebook
                            ),

                            title=(
                                clip.get(
                                    'title',
                                    f'AI Video Repurposer '
                                    f'Clip {clip["number"]}',
                                )
                            ),

                            description=(
                                clip.get(
                                    'description',
                                    'A short-form video created '
                                    'with AI Video Repurposer.',
                                )
                                + '\n\n'
                                + ' '.join(
                                    clip.get('hashtags', [])
                                )
                            ),

                            scheduled_at=(
                                scheduled_at
                            ),

                            status='scheduled',

                            external_post_id=(
                                facebook_result.get(
                                    'video_id'
                                )
                            ),
                        )
                    )

                    result = {
                        'clip_number': (
                            clip['number']
                        ),

                        'video_id': (
                            facebook_result.get(
                                'video_id'
                            )
                        ),

                        'scheduled_post_id': (
                            scheduled_post.id
                        ),

                        'scheduled_at': (
                            scheduled_at.isoformat()
                        ),

                        'status': 'scheduled',

                        'error': '',
                    }

                    facebook_publish_results.append(
                        result
                    )

                    print(
                        'Facebook video scheduled '
                        'successfully:'
                    )

                    print(
                        result
                    )

                except Exception as exc:

                    error_message = str(
                        exc
                    )

                    result = {
                        'clip_number': (
                            clip['number']
                        ),

                        'video_id': None,

                        'scheduled_post_id': None,

                        'scheduled_at': (
                            scheduled_at.isoformat()
                        ),

                        'status': 'failed',

                        'error': error_message,
                    }

                    facebook_publish_results.append(
                        result
                    )

                    platform_errors.append(
                        {
                            'platform': 'facebook',

                            'clip_number': (
                                clip['number']
                            ),

                            'error': error_message,
                        }
                    )

                    print(
                        'Facebook scheduling failed '
                        f'for clip {clip["number"]}:'
                    )

                    print(
                        error_message
                    )

                    print(
                        'Continuing with the next '
                        'Facebook clip...'
                    )


    # ========================================================
    # X
    # ========================================================

    if (
        generated_clips
        and video.selected_x_account
    ):

        selected_x_account = (
            video.selected_x_account
        )

        for clip in generated_clips:

            try:

                print(
                    'Starting X video upload '
                    f'for clip {clip["number"]}...'
                )

                x_result = (
                    publish_video_to_x(
                        x_credential=(
                            selected_x_account
                        ),

                        video_path=(
                            clip['file_path']
                        ),

                        text=(
                            clip.get(
                                'title',
                                f'AI Video Repurposer '
                                f'Clip {clip["number"]}',
                            )
                            + '\n\n'
                            + ' '.join(
                                clip.get('hashtags', [])
                            )
                        ),
                    )
                )

                post_id = (
                    x_result.get(
                        'post_id'
                    )
                )

                username = (
                    selected_x_account.username
                    or ''
                )

                post_url = ''

                if (
                    username
                    and post_id
                ):

                    post_url = (
                        'https://x.com/'
                        f'{username}/status/'
                        f'{post_id}'
                    )

                absolute_path = Path(
                    clip['file_path']
                )

                relative_path = (
                    absolute_path.relative_to(
                        settings.MEDIA_ROOT
                    )
                )

                video_path = (
                    str(relative_path)
                    .replace('\\', '/')
                )

                scheduled_post = (
                    ScheduledPost.objects.create(
                        user=video.user,
                        video=video,

                        video_path=video_path,

                        platform='x',

                        x_account=(
                            selected_x_account
                        ),

                        title=(
                            f'AI Video Repurposer '
                            f'Clip {clip["number"]}'
                        ),

                        description=(
                            'Published automatically '
                            'by AI Video Repurposer.'
                        ),

                        scheduled_at=(
                            timezone.now()
                        ),

                        status='published',

                        external_post_id=(
                            post_id or ''
                        ),

                        external_url=(
                            post_url
                        ),
                    )
                )

                result = {
                    'clip_number': (
                        clip['number']
                    ),

                    'post_id': post_id,

                    'url': post_url,

                    'scheduled_post_id': (
                        scheduled_post.id
                    ),

                    'status': 'published',

                    'error': '',
                }

                x_publish_results.append(
                    result
                )

                print(
                    'X video published successfully:'
                )

                print(
                    result
                )

            except Exception as exc:

                error_message = str(
                    exc
                )

                result = {
                    'clip_number': (
                        clip['number']
                    ),

                    'post_id': None,

                    'url': '',

                    'scheduled_post_id': None,

                    'status': 'failed',

                    'error': error_message,
                }

                x_publish_results.append(
                    result
                )

                platform_errors.append(
                    {
                        'platform': 'x',

                        'clip_number': (
                            clip['number']
                        ),

                        'error': error_message,
                    }
                )

                print(
                    'X video publishing failed '
                    f'for clip {clip["number"]}:'
                )

                print(
                    error_message
                )

                print(
                    'Continuing with the next '
                    'X clip...'
                )


    # ========================================================
    # FINAL AUTO SYSTEM RESULT
    # ========================================================

    if platform_errors:

        print(
            'Auto System completed with platform errors.'
        )

        for error in platform_errors:

            print(
                error
            )

    else:

        print(
            'Auto System completed successfully.'
        )


    return {
        'youtube': (
            youtube_upload_results
        ),

        'instagram': (
            instagram_publish_results
        ),

        'facebook': (
            facebook_publish_results
        ),

        'x': (
            x_publish_results
        ),

        'errors': (
            platform_errors
        ),
    }


# ============================================================
# VERTICAL SHORT COMPOSITOR
# ============================================================


def apply_vertical_compositor(video, generated_clips):
    """Create 9:16 versions without overwriting original clips."""

    if not generated_clips:
        return generated_clips

    output_directory = (
        Path(settings.MEDIA_ROOT)
        / "processed"
        / "vertical"
        / str(video.id)
    )
    output_directory.mkdir(parents=True, exist_ok=True)

    bgm_path = (
        Path(settings.MEDIA_ROOT)
        / "background_music"
        / "background_music.mp3"
    )

    processed_clips = []

    for clip in generated_clips:
        source_path = Path(clip["file_path"])

        if not source_path.is_file():
            raise FileNotFoundError(
                f"Source clip not found: {source_path}"
            )

        output_path = (
            output_directory
            / f"vertical_clip_{clip['number']}.mp4"
        )

        print(
            "Creating vertical 9:16 clip "
            f"{clip['number']}..."
        )

        create_vertical_short(
            input_path=source_path,
            output_path=output_path,
            bgm_path=bgm_path if bgm_path.is_file() else None,
        )

        updated_clip = dict(clip)

        relative_path = output_path.relative_to(
            settings.MEDIA_ROOT
        )

        media_path = str(relative_path).replace("\\", "/")

        updated_clip["file_path"] = str(output_path)
        updated_clip["path"] = (
            f"{settings.MEDIA_URL}{media_path}"
        )

        processed_clips.append(updated_clip)

    print(
        "Vertical compositor completed for "
        f"{len(processed_clips)} clip(s)."
    )

    return processed_clips


# ============================================================
# BACKGROUND PROCESSING
# ============================================================


def background_process_video(
    video_id,
):
    """
    Process the video in a background thread.

    Processing errors are fatal.

    Platform publishing errors are NOT fatal to the video
    processing workflow.
    """

    try:

        video = Video.objects.get(
            id=video_id
        )

        print(
            f'Background processing started '
            f'for Video ID {video_id}.'
        )


        # ====================================================
        # VIDEO PROCESSING
        # ====================================================

        generated_clips = (
            process_video(
                video
            )
        )

        # Create separate 9:16 versions for publishing.
        # Original generated clips are never overwritten.
        generated_clips = apply_vertical_compositor(
            video,
            generated_clips,
        )


        video.refresh_from_db()


        # ====================================================
        # MANUAL SYSTEM
        # ====================================================

        if video.workflow_mode == 'manual':

            video.processing_progress = 100

            video.processing_stage = (
                'Processing completed'
            )

            video.status = 'completed'

            video.save(
                update_fields=[
                    'processing_progress',
                    'processing_stage',
                    'status',
                    'updated_at',
                ]
            )

            print(
                'Manual System processing completed.'
            )

            return


        # ====================================================
        # AUTO SYSTEM
        # ====================================================

        video.processing_progress = 96

        video.processing_stage = (
            'Generating AI titles, descriptions and hashtags'
        )

        video.save(
            update_fields=[
                'processing_progress',
                'processing_stage',
                'updated_at',
            ]
        )

        generated_clips = enrich_clips_with_ai_metadata(
            generated_clips
        )

        video.processing_progress = 98

        video.processing_stage = (
            'Scheduling generated clips'
        )

        video.save(
            update_fields=[
                'processing_progress',
                'processing_stage',
                'updated_at',
            ]
        )


        auto_result = (
            run_auto_publishing(
                video,
                generated_clips,
            )
        )


        # ====================================================
        # FINAL STATUS
        # ====================================================

        video.refresh_from_db()

        platform_errors = (
            auto_result.get(
                'errors',
                []
            )
        )


        if platform_errors:

            failed_platforms = sorted(
                set(
                    error['platform']
                    for error in platform_errors
                )
            )

            failed_platform_text = (
                ', '.join(
                    failed_platforms
                )
            )

            video.processing_stage = (
                'Completed with platform errors: '
                f'{failed_platform_text}'
            )

        else:

            video.processing_stage = (
                'Processing and scheduling completed'
            )


        video.processing_progress = 100

        # IMPORTANT:
        #
        # The video itself processed successfully.
        # Therefore platform publishing failures do not
        # make the entire video "failed".

        video.status = 'completed'


        video.save(
            update_fields=[
                'processing_progress',
                'processing_stage',
                'status',
                'updated_at',
            ]
        )


        print(
            'BACKGROUND PROCESSING COMPLETED.'
        )


        if platform_errors:

            print(
                'Video processing completed, but some '
                'platform operations failed.'
            )

        else:

            print(
                'Video processing and Auto System '
                'operations completed successfully.'
            )


    except Exception as exc:

        print(
            'BACKGROUND PROCESSING ERROR:'
        )

        print(
            str(exc)
        )


        try:

            video = Video.objects.get(
                id=video_id
            )

            video.status = 'failed'

            video.processing_progress = 100

            video.processing_stage = (
                f'Processing failed: {exc}'
            )

            video.save(
                update_fields=[
                    'status',
                    'processing_progress',
                    'processing_stage',
                    'updated_at',
                ]
            )

        except Exception:

            pass


def get_generated_clips(video):
    """
    Load generated clips from the video's output folder
    for display on the dashboard.
    """

    output_directory = (
        Path(settings.MEDIA_ROOT)
        / "processed"
        / video.processing_mode
        / str(video.id)
    )

    if not output_directory.exists():
        return []

    # Prefer the separate 9:16 compositor outputs when available.
    vertical_directory = (
        Path(settings.MEDIA_ROOT)
        / "processed"
        / "vertical"
        / str(video.id)
    )

    vertical_clip_files = []
    if vertical_directory.exists():
        vertical_clip_files = list(
            vertical_directory.glob("vertical_clip_*.mp4")
        )

    if vertical_clip_files:
        clip_files = vertical_clip_files
    elif video.processing_mode == "normal":
        clip_files = list(
            output_directory.glob("normal_clip_*.mp4")
        )
    elif video.processing_mode == "viral":
        clip_files = list(
            output_directory.glob("clip_*.mp4")
        )
    else:
        return []

    # Sort clips numerically: 1, 2, 3 ... 10
    def clip_number(file_path):
        try:
            return int(
                file_path.stem.split("_")[-1]
            )
        except (ValueError, IndexError):
            return 0

    clip_files.sort(key=clip_number)

    generated_clips = []

    for index, clip_file in enumerate(
        clip_files,
        start=1
    ):
        relative_path = clip_file.relative_to(
            settings.MEDIA_ROOT
        )

        media_path = str(
            relative_path
        ).replace("\\", "/")

        generated_clips.append({
            "number": index,
            "path": (
                f"{settings.MEDIA_URL}{media_path}"
            ),
            "file_path": str(clip_file),
            "duration": "—",
            "text": (
                "Normal sequential cut"
                if video.processing_mode == "normal"
                else "Viral clip"
            ),
            "score": "—",
        })

    return generated_clips


# ============================================================
# HOME / DASHBOARD
# ============================================================


def home(request):

    current_user_id = (
        request.user.id
        if request.user.is_authenticated
        else -1
    )

    form = _configure_video_form_for_user(
        VideoUploadForm(),
        request.user,
    )

    if request.method == "POST" and not request.user.is_authenticated:
        login_url = reverse("login")
        next_url = request.get_full_path()
        return redirect(
            f"{login_url}?next={quote(next_url, safe='')}"
        )

    generated_clips = []

    youtube_upload_results = []

    instagram_publish_results = []

    facebook_publish_results = []

    x_publish_results = []

    video = None


    # ========================================================
    # EXISTING VIDEO FROM QUERY PARAMETER
    # ========================================================

    video_id = request.GET.get(
        'video_id'
    )

    if video_id:

        try:

            if not request.user.is_authenticated:
                video = None
            else:
                video = Video.objects.get(
                    id=int(video_id),
                    user=request.user,
                )

        except (
            ValueError,
            TypeError,
            Video.DoesNotExist,
        ):

            video = None


        if (
            video
            and video.status == 'completed'
        ):

            generated_clips = (
                get_generated_clips(
                    video
                )
            )


    # ========================================================
    # POST
    # ========================================================

    if request.method == 'POST':

        form = _configure_video_form_for_user(
            VideoUploadForm(
                request.POST,
                request.FILES,
            ),
            request.user,
        )


        print(
            'YOUTUBE CHANNEL POST VALUE:',
            request.POST.get(
                'selected_youtube_channel'
            )
        )


        if form.is_valid():

            video = form.save(commit=False)
            video.user = request.user

            workflow_mode = form.cleaned_data.get(
                "workflow_mode",
                "auto",
            )

            auto_platforms = form.cleaned_data.get(
                "auto_platforms",
            ) or []

            video.workflow_mode = workflow_mode

            # ------------------------------------------------
            # Auto System platform selection
            # ------------------------------------------------
            # Only accounts whose platform is selected are saved.
            # Manual System deliberately clears these fields because
            # its platform/account choice happens after processing.

            if workflow_mode == "auto":
                if "youtube" not in auto_platforms:
                    video.selected_youtube_channel = None
                if "instagram" not in auto_platforms:
                    video.selected_instagram_account = None
                if "facebook" not in auto_platforms:
                    video.selected_facebook_page = None
                if "x" not in auto_platforms:
                    video.selected_x_account = None
            else:
                video.selected_youtube_channel = None
                video.selected_instagram_account = None
                video.selected_facebook_page = None
                video.selected_x_account = None

            video.save()

            # ------------------------------------------------
            # Initial processing state
            # ------------------------------------------------

            video.status = 'processing'

            video.processing_progress = 0

            video.processing_stage = (
                'Starting processing'
            )

            video.save(
                update_fields=[
                    'status',
                    'processing_progress',
                    'processing_stage',
                    'updated_at',
                ]
            )


            # ------------------------------------------------
            # Start background processing
            # ------------------------------------------------

            processing_thread = threading.Thread(
                target=background_process_video,

                args=(
                    video.id,
                ),

                daemon=True,
            )

            processing_thread.start()


            print(
                f'Background processing started '
                f'for Video ID {video.id}.'
            )


            # ------------------------------------------------
            # POST -> REDIRECT -> GET
            # ------------------------------------------------
            #
            # Keep the browser on a GET request after starting
            # processing. This prevents refresh/re-submit from
            # creating another Video and another processing job.

            return redirect(
                f'/?video_id={video.id}'
            )


    # ========================================================
    # CONNECTED ACCOUNTS
    # ========================================================

    youtube_channels = (
        YouTubeCredential.objects.filter(
            user_id=current_user_id,
            is_active=True
        )
    )

    youtube_dashboard_analytics = (
        get_youtube_dashboard_analytics(
            youtube_channels
        )
    )

    instagram_accounts = (
        InstagramCredential.objects.filter(
            user_id=current_user_id,
            is_active=True
        )
    )

    facebook_pages = (
        FacebookCredential.objects.filter(
            user_id=current_user_id,
            is_active=True
        )
    )

    x_accounts = (
        XCredential.objects.filter(
            owner_id=current_user_id,
            is_active=True
        )
    )

    # ========================================================
    # REAL DASHBOARD STATISTICS
    # ========================================================
    #
    # These values come directly from the database.
    # Existing processing/publishing logic is untouched.
    #

    total_videos = Video.objects.filter(user_id=current_user_id).count()

    processing_videos = Video.objects.filter(
        user_id=current_user_id,
        status='processing'
    ).count()

    completed_videos = Video.objects.filter(
        user_id=current_user_id,
        status='completed'
    ).count()

    failed_videos = Video.objects.filter(
        user_id=current_user_id,
        status='failed'
    ).count()

    uploaded_videos = Video.objects.filter(
        user_id=current_user_id,
        status='uploaded'
    ).count()

    # Only future scheduled posts are considered "Scheduled".
    # Past records are kept in the database and are not modified.
    current_time = timezone.now()

    scheduled_posts = ScheduledPost.objects.filter(
        user_id=current_user_id,
        status='scheduled',
        scheduled_at__gt=current_time,
    ).count()

    published_posts = ScheduledPost.objects.filter(
        user_id=current_user_id,
        status='published'
    ).count()

    failed_posts = ScheduledPost.objects.filter(
        user_id=current_user_id,
        status='failed'
    ).count()

    processing_posts = ScheduledPost.objects.filter(
        user_id=current_user_id,
        status='processing'
    ).count()

    cancelled_posts = ScheduledPost.objects.filter(
        user_id=current_user_id,
        status='cancelled'
    ).count()

    # Platform-wise post statistics

    youtube_posts = ScheduledPost.objects.filter(
        user_id=current_user_id,
        platform='youtube'
    ).count()

    instagram_posts = ScheduledPost.objects.filter(
        user_id=current_user_id,
        platform='instagram'
    ).count()

    facebook_posts = ScheduledPost.objects.filter(
        user_id=current_user_id,
        platform='facebook'
    ).count()

    x_posts = ScheduledPost.objects.filter(
        user_id=current_user_id,
        platform='x'
    ).count()

    youtube_published = ScheduledPost.objects.filter(
        user_id=current_user_id,
        platform='youtube',
        status='published'
    ).count()

    instagram_published = ScheduledPost.objects.filter(
        user_id=current_user_id,
        platform='instagram',
        status='published'
    ).count()

    facebook_published = ScheduledPost.objects.filter(
        user_id=current_user_id,
        platform='facebook',
        status='published'
    ).count()

    x_published = ScheduledPost.objects.filter(
        user_id=current_user_id,
        platform='x',
        status='published'
    ).count()

    # Recent dashboard records

    recent_videos = Video.objects.filter(
        user_id=current_user_id
    ).order_by(
        '-created_at'
    )[:8]

    recent_scheduled_posts = (
        ScheduledPost.objects
        .filter(
            user_id=current_user_id,
            status='scheduled',
            scheduled_at__gt=current_time,
        )
        .select_related(
            'video',
            'youtube_channel',
            'instagram_account',
            'facebook_page',
            'x_account',
        )
        .order_by('scheduled_at')[:8]
    )

    recent_published_posts = (
        ScheduledPost.objects
        .filter(
            user_id=current_user_id,
            status='published'
        )
        .select_related(
            'video',
            'youtube_channel',
            'instagram_account',
            'facebook_page',
            'x_account',
        )
        .order_by('-updated_at')[:8]
    )

    recent_failed_posts = (
        ScheduledPost.objects
        .filter(
            user_id=current_user_id,
            status='failed'
        )
        .select_related(
            'video',
            'youtube_channel',
            'instagram_account',
            'facebook_page',
            'x_account',
        )
        .order_by('-updated_at')[:8]
    )

    dashboard_stats = {
        'total_videos': total_videos,
        'processing_videos': processing_videos,
        'completed_videos': completed_videos,
        'failed_videos': failed_videos,
        'uploaded_videos': uploaded_videos,

        'scheduled_posts': scheduled_posts,
        'published_posts': published_posts,
        'failed_posts': failed_posts,
        'processing_posts': processing_posts,
        'cancelled_posts': cancelled_posts,

        'youtube_posts': youtube_posts,
        'instagram_posts': instagram_posts,
        'facebook_posts': facebook_posts,
        'x_posts': x_posts,

        'youtube_published': youtube_published,
        'instagram_published': instagram_published,
        'facebook_published': facebook_published,
        'x_published': x_published,
    }
    # ========================================================
    # YOUTUBE TREND INTELLIGENCE
    # ========================================================
    #
    # Read public YouTube most-popular videos for India.
    # This is read-only and does not publish or schedule anything.
    #
    # If a YouTube Data API key is not configured, the dashboard
    # continues normally and Gemini receives an unavailable state.
    #

    youtube_trend_context = {
        "available": False,
        "status": "api_key_not_configured",
        "error": (
            "YouTube Data API key is not configured. "
            "Trend intelligence is unavailable."
        ),
        "region_code": "IN",
        "videos": [],
    }

    try:
        youtube_api_key = getattr(
            settings,
            "YOUTUBE_API_KEY",
            "",
        )

        if youtube_api_key:
            youtube_trend_context = get_youtube_trend_context(
                api_key=youtube_api_key,
                region_code="IN",
                max_results=25,
            )

    except Exception as exc:
        print(
            "YouTube Trend Intelligence generation failed:",
            exc,
        )

        youtube_trend_context = {
            "available": False,
            "status": "api_error",
            "error": str(exc),
            "region_code": "IN",
            "videos": [],
        }

    # ========================================================
    # AI DASHBOARD SUGGESTIONS
    # ========================================================
    #
    # Only verified dashboard data is sent to Gemini.
    # Existing publishing/processing logic is untouched.
    #

    ai_dashboard_context = {
        "dashboard_stats": dashboard_stats,

        "youtube_analytics": youtube_dashboard_analytics,

        "youtube_trends": youtube_trend_context,

        "connected_accounts": {
            "youtube": youtube_channels.count(),
            "instagram": instagram_accounts.count(),
            "facebook": facebook_pages.count(),
            "x": x_accounts.count(),
        },

        "recent_activity": {
            "recent_videos_count": recent_videos.count(),
            "scheduled_posts_count": recent_scheduled_posts.count(),
            "published_posts_count": recent_published_posts.count(),
            "failed_posts_count": recent_failed_posts.count(),
        },
    }

    ai_suggestions = []

    try:
        ai_result = generate_dashboard_ai_suggestions(
            ai_dashboard_context
        )

        ai_suggestions = ai_result.get(
            "suggestions",
            []
        )

    except Exception as exc:
        print(
            "Dashboard AI Suggestions generation failed:",
            exc,
        )

        ai_suggestions = []

    return render(
        request,
        'home.html',
        {
            'form': form,

            'youtube_channels': (
                youtube_channels
            ),

            'instagram_accounts': (
                instagram_accounts
            ),

            'facebook_pages': (
                facebook_pages
            ),

            'x_accounts': (
                x_accounts
            ),

            'generated_clips': (
                generated_clips
            ),

            'youtube_upload_results': (
                youtube_upload_results
            ),

            'instagram_publish_results': (
                instagram_publish_results
            ),

            'facebook_publish_results': (
                facebook_publish_results
            ),

            'x_publish_results': (
                x_publish_results
            ),

            # Real dashboard data
            'dashboard_stats': dashboard_stats,
            'youtube_dashboard_analytics': (
                youtube_dashboard_analytics
            ),
            'youtube_trend_context': youtube_trend_context,
            'ai_suggestions': ai_suggestions,
            'total_videos': total_videos,
            'processing_videos': processing_videos,
            'completed_videos': completed_videos,
            'failed_videos': failed_videos,
            'uploaded_videos': uploaded_videos,

            'scheduled_posts': scheduled_posts,
            'published_posts': published_posts,
            'failed_posts': failed_posts,
            'processing_posts': processing_posts,
            'cancelled_posts': cancelled_posts,

            'youtube_posts': youtube_posts,
            'instagram_posts': instagram_posts,
            'facebook_posts': facebook_posts,
            'x_posts': x_posts,

            'youtube_published': youtube_published,
            'instagram_published': instagram_published,
            'facebook_published': facebook_published,
            'x_published': x_published,

            'recent_videos': recent_videos,
            'recent_scheduled_posts': recent_scheduled_posts,
            'recent_published_posts': recent_published_posts,
            'recent_failed_posts': recent_failed_posts,

            'video': video,
        }
    )


# ============================================================
# MANUAL PUBLISH
# ============================================================


@login_required(login_url="login")
def manual_publish(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POST required."},
            status=405,
        )

    try:
        video = get_object_or_404(
            Video,
            id=request.POST.get("video_id"),
            user=request.user,
        )
        if video.status != "completed":
            raise ValueError("Video processing is not completed yet.")

        clips, platforms, accounts = _manual_selection(request, video)
        results = []

        for clip in clips:
            for platform in platforms:
                try:
                    publish_result = _publish_manual_clip(
                        video, clip, platform, accounts[platform]
                    )
                    scheduled_post = ScheduledPost.objects.create(
                        user=video.user,
                        video=video,
                        video_path=_relative_clip_path(clip),
                        platform=platform,
                        youtube_channel=(
                            accounts[platform]
                            if platform == "youtube" else None
                        ),
                        instagram_account=(
                            accounts[platform]
                            if platform == "instagram" else None
                        ),
                        facebook_page=(
                            accounts[platform]
                            if platform == "facebook" else None
                        ),
                        x_account=(
                            accounts[platform]
                            if platform == "x" else None
                        ),
                        title=f"AI Video Repurposer Clip {clip['number']}",
                        description="Published manually by AI Video Repurposer.",
                        scheduled_at=timezone.now(),
                        status="published",
                        external_post_id=publish_result.get(
                            "external_post_id",
                            publish_result.get("video_id", ""),
                        ),
                        external_url=publish_result.get(
                            "external_url",
                            "",
                        ),
                    )
                    results.append({
                        "clip_number": clip["number"],
                        "platform": platform,
                        "status": "published",
                        "scheduled_post_id": scheduled_post.id,
                    })
                except Exception as exc:
                    results.append({
                        "clip_number": clip["number"],
                        "platform": platform,
                        "status": "failed",
                        "error": str(exc),
                    })

        return JsonResponse({
            "success": True,
            "results": results,
        })

    except Exception as exc:
        return JsonResponse({
            "success": False,
            "error": str(exc),
        }, status=400)


# ============================================================
# MANUAL SCHEDULE
# ============================================================


@login_required(login_url="login")
def manual_schedule(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POST required."},
            status=405,
        )

    try:
        video = get_object_or_404(
            Video,
            id=request.POST.get("video_id"),
            user=request.user,
        )
        if video.status != "completed":
            raise ValueError("Video processing is not completed yet.")

        clips, platforms, accounts = _manual_selection(request, video)
        schedule_value = request.POST.get("scheduled_at", "").strip()

        if not schedule_value:
            raise ValueError("Select a schedule date and time.")

        try:
            scheduled_at = datetime.fromisoformat(
                schedule_value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "Invalid schedule date/time."
            ) from exc

        if timezone.is_naive(scheduled_at):
            scheduled_at = timezone.make_aware(
                scheduled_at,
                timezone.get_current_timezone(),
            )

        if scheduled_at <= timezone.now():
            raise ValueError("Schedule time must be in the future.")

        caption = request.POST.get(
            "caption",
            "Generated by AI Video Repurposer.",
        ).strip()

        results = []

        for clip in clips:
            for platform in platforms:
                title = f"AI Video Repurposer Clip {clip['number']}"
                external_post_id = ""
                external_url = ""

                # YouTube requires the video to be uploaded now as
                # private, with a future publishAt timestamp.
                # The local scheduler does not perform YouTube uploads.
                if platform == "youtube":
                    upload_result = upload_video_to_youtube(
                        video_path=clip["file_path"],
                        token_json=accounts["youtube"].token_json,
                        title=title,
                        description=caption,
                        tags=[
                            "AI Video Repurposer",
                            "Short Video",
                            "Video Repurposing",
                        ],
                        privacy_status="private",
                        publish_at=scheduled_at,
                    )
                    external_post_id = upload_result.get("video_id") or ""
                    external_url = upload_result.get("url") or ""

                post = ScheduledPost.objects.create(
                        user=video.user,
                    video=video,
                    video_path=_relative_clip_path(clip),
                    platform=platform,
                    youtube_channel=(
                        accounts[platform]
                        if platform == "youtube" else None
                    ),
                    instagram_account=(
                        accounts[platform]
                        if platform == "instagram" else None
                    ),
                    facebook_page=(
                        accounts[platform]
                        if platform == "facebook" else None
                    ),
                    x_account=(
                        accounts[platform]
                        if platform == "x" else None
                    ),
                    title=title,
                    description=caption,
                    scheduled_at=scheduled_at,
                    status="scheduled",
                    external_post_id=external_post_id,
                    external_url=external_url,
                )

                results.append({
                    "clip_number": clip["number"],
                    "platform": platform,
                    "status": "scheduled",
                    "scheduled_post_id": post.id,
                    "scheduled_at": scheduled_at.isoformat(),
                    "external_post_id": external_post_id,
                    "external_url": external_url,
                })

        return JsonResponse({
            "success": True,
            "results": results,
        })

    except Exception as exc:
        return JsonResponse({
            "success": False,
            "error": str(exc),
        }, status=400)


# ============================================================
# PROCESSING STATUS API
# ============================================================


@login_required(login_url="login")
def processing_status(
    request,
    video_id,
):

    video = get_object_or_404(
        Video,
        id=video_id,
        user=request.user,
    )


    return JsonResponse(
        {
            'id': video.id,

            'status': (
                video.status
            ),

            'progress': (
                video.processing_progress
            ),

            'stage': (
                video.processing_stage
            ),

            'processing_mode': (
                video.processing_mode
            ),

            'workflow_mode': (
                video.workflow_mode
            ),

            'completed': (
                video.status == 'completed'
            ),

            'failed': (
                video.status == 'failed'
            ),
        }
    )


# ============================================================
# YOUTUBE LOGIN
# ============================================================


@login_required(login_url="login")
def youtube_login(request):

    (
        authorization_url,
        state,
        code_verifier,
    ) = get_youtube_authorization_url()


    request.session[
        'youtube_oauth_state'
    ] = state


    request.session[
        'youtube_code_verifier'
    ] = code_verifier


    return redirect(
        authorization_url
    )


@login_required(login_url="login")
def youtube_callback(request):

    credentials = (
        exchange_code_for_credentials(
            request
        )
    )


    channel_info = (
        get_youtube_channel_info(
            credentials
        )
    )


    channel_id = channel_info.get("channel_id")
    youtube_credential = None

    if channel_id:
        youtube_credential = YouTubeCredential.objects.filter(
            channel_id=channel_id
        ).first()

    if youtube_credential is None:
        channel_name = channel_info.get("channel_name") or channel_info.get("title")
        if channel_name:
            youtube_credential = YouTubeCredential.objects.filter(
                channel_name=channel_name
            ).first()

    if youtube_credential is not None and youtube_credential.user_id not in (None, request.user.id):
        return HttpResponse(
            "This YouTube channel is already connected to another user.",
            status=400,
            content_type="text/plain",
        )

    save_youtube_credentials(
        credentials,
        channel_info
    )

    if channel_id:
        youtube_credential = YouTubeCredential.objects.filter(
            channel_id=channel_id
        ).first()

    if youtube_credential is None:
        channel_name = channel_info.get("channel_name") or channel_info.get("title")
        if channel_name:
            youtube_credential = YouTubeCredential.objects.filter(
                channel_name=channel_name
            ).first()

    if youtube_credential is not None:
        youtube_credential.user = request.user
        youtube_credential.save(update_fields=["user"])


    return render(
        request,
        'youtube_callback.html',
        {
            'channel_info': channel_info,
        }
    )


# ============================================================
# INSTAGRAM LOGIN
# ============================================================


@login_required(login_url="login")
def instagram_login(request):

    authorization_url = (
        get_instagram_authorization_url()
    )


    return redirect(
        authorization_url
    )


@login_required(login_url="login")
def instagram_callback(request):

    code = request.GET.get(
        'code'
    )


    if not code:

        raise ValueError(
            'Instagram authorization code is missing.'
        )


    # --------------------------------------------------------
    # Authorization code -> short-lived token
    # --------------------------------------------------------

    short_token_data = (
        exchange_instagram_code_for_token(
            code
        )
    )


    short_lived_token = (
        short_token_data.get(
            'access_token'
        )
    )


    if not short_lived_token:

        raise ValueError(
            'Instagram access token was not returned.'
        )


    # --------------------------------------------------------
    # Short-lived token -> long-lived token
    # --------------------------------------------------------

    long_token_data = (
        exchange_short_lived_token_for_long_lived_token(
            short_lived_token
        )
    )


    long_lived_token = (
        long_token_data.get(
            'access_token'
        )
    )


    if not long_lived_token:

        raise ValueError(
            'Instagram long-lived access token '
            'was not returned.'
        )


    # --------------------------------------------------------
    # Get account information
    # --------------------------------------------------------

    account_info = (
        get_instagram_account_info(
            long_lived_token
        )
    )


    # --------------------------------------------------------
    # Save credentials
    # --------------------------------------------------------

    instagram_account_id = account_info.get("id") or account_info.get("account_id")
    existing_instagram = None

    if instagram_account_id:
        existing_instagram = InstagramCredential.objects.filter(
            account_id=instagram_account_id
        ).first()

    if existing_instagram is not None and existing_instagram.user_id not in (None, request.user.id):
        return HttpResponse(
            "This Instagram account is already connected to another user.",
            status=400,
            content_type="text/plain",
        )

    instagram_credential = (
        save_instagram_credentials(
            long_lived_token,
            long_token_data,
            account_info,
        )
    )

    if instagram_credential is not None:
        instagram_credential.user = request.user
        instagram_credential.save(update_fields=["user"])


    return render(
        request,
        'instagram_callback.html',
        {
            'account_info': account_info,

            'instagram_credential': (
                instagram_credential
            ),
        }
    )


# ============================================================
# FACEBOOK LOGIN FOR BUSINESS
# ============================================================


@login_required(login_url="login")
def facebook_login(request):

    state = signing.TimestampSigner(
        salt='facebook-oauth-state'
    ).sign(
        create_facebook_oauth_state()
    )


    request.session[
        'facebook_oauth_pending'
    ] = True


    authorization_url = (
        get_facebook_authorization_url(
            state
        )
    )


    return redirect(
        authorization_url
    )


# ============================================================
# SAVE FACEBOOK + INSTAGRAM CREDENTIALS
# ============================================================


def _save_facebook_and_instagram_credentials(
    user_access_token,
    owner,
):

    pages = (
        get_facebook_pages(
            user_access_token
        )
    )


    for page in pages:

        page_id = page.get(
            'id'
        )

        page_name = page.get(
            'name'
        )

        page_access_token = page.get(
            'access_token'
        )

        instagram_account_id = page.get(
            'instagram_account_id'
        )


        if not page_id:

            continue


        if not page_name:

            continue


        if not page_access_token:

            continue


        # ====================================================
        # FACEBOOK PAGE
        # ====================================================

        facebook_token_json = json.dumps(
            {
                'access_token': (
                    page_access_token
                ),

                'user_access_token': (
                    user_access_token
                ),

                'page_id': page_id,

                'page_name': page_name,

                'instagram_account_id': (
                    instagram_account_id
                ),

                'auth_method': (
                    'facebook_login_for_business'
                ),
            }
        )


        existing_facebook = FacebookCredential.objects.filter(
            page_id=page_id
        ).first()

        if existing_facebook is not None and existing_facebook.user_id not in (None, owner.id):
            continue

        FacebookCredential.objects.update_or_create(
            page_id=page_id,

            defaults={
                'page_name': page_name,

                'token_json': (
                    facebook_token_json
                ),

                'is_active': True,
                'user': owner,
            }
        )


        # ====================================================
        # LINKED INSTAGRAM
        # ====================================================

        if instagram_account_id:

            instagram_token_json = json.dumps(
                {
                    'access_token': (
                        page_access_token
                    ),

                    'user_access_token': (
                        user_access_token
                    ),

                    'account_id': (
                        instagram_account_id
                    ),

                    'account_name': (
                        page_name
                    ),

                    'page_id': page_id,

                    'page_name': page_name,

                    'auth_method': (
                        'facebook_login_for_business'
                    ),
                }
            )


            existing_instagram = InstagramCredential.objects.filter(
                account_id=instagram_account_id
            ).first()

            if existing_instagram is not None and existing_instagram.user_id not in (None, owner.id):
                continue

            InstagramCredential.objects.update_or_create(
                account_id=instagram_account_id,

                defaults={
                    'account_name': (
                        page_name
                    ),

                    'token_json': (
                        instagram_token_json
                    ),

                    'is_active': True,
                    'user': owner,
                }
            )


            print(
                'Linked Instagram Professional '
                'Account saved successfully:'
            )


            print(
                {
                    'account_id': (
                        instagram_account_id
                    ),

                    'page_id': page_id,

                    'page_name': page_name,
                }
            )


    return pages


# ============================================================
# FACEBOOK CALLBACK
# ============================================================

@login_required(login_url="login")
def facebook_callback(request):
    """
    Handle Facebook Login for Business callback.

    Meta's response_type=token flow returns the token in the
    browser URL fragment.

    The fragment is not sent to Django automatically.

    Therefore:

    GET:
        JavaScript reads the fragment.

    POST:
        JavaScript sends the token to Django using the same-origin
        POST request.
    """


    # ========================================================
    # GET
    # ========================================================

    if request.method == 'GET':

        csrf_token = get_token(
            request
        )


        return HttpResponse(
            f"""
<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1.0"
    >

    <title>
        Completing Facebook Login
    </title>


    <style>

        body {{

            margin: 0;

            min-height: 100vh;

            display: flex;

            align-items: center;

            justify-content: center;

            font-family:
                Arial,
                Helvetica,
                sans-serif;

            background: #f4f6f9;

            color: #1f2937;
        }}


        .card {{

            width: min(
                560px,
                90%
            );

            padding: 32px;

            border-radius: 14px;

            background: white;

            box-shadow:
                0 8px 30px
                rgba(
                    0,
                    0,
                    0,
                    0.08
                );

            text-align: center;
        }}


        .error {{

            color: #b91c1c;

            margin-top: 14px;

            line-height: 1.5;
        }}


        .success {{

            color: #15803d;

            margin-top: 14px;
        }}

    </style>

</head>


<body>


    <div class="card">

        <h1>
            Connecting Facebook
        </h1>


        <p id="message">
            Completing Business Login...
        </p>

    </div>


    <script>

        const csrfToken =
            "{csrf_token}";


        const messageElement =
            document.getElementById(
                "message"
            );


        function showError(message) {{

            messageElement.className =
                "error";

            messageElement.innerText =
                message;

        }}


        function showSuccess(message) {{

            messageElement.className =
                "success";

            messageElement.innerText =
                message;

        }}


        async function completeBusinessLogin() {{

            try {{

                const fragment =
                    window.location.hash.substring(
                        1
                    );


                if (!fragment) {{

                    showError(
                        "Facebook did not return an access token. " +
                        "Please start the Facebook connection again."
                    );

                    return;
                }}


                const fragmentParams =
                    new URLSearchParams(
                        fragment
                    );


                const accessToken =
                    fragmentParams.get(
                        "access_token"
                    );


                const longLivedToken =
                    fragmentParams.get(
                        "long_lived_token"
                    );


                const expiresIn =
                    fragmentParams.get(
                        "expires_in"
                    ) || "";


                const dataAccessExpirationTime =
                    fragmentParams.get(
                        "data_access_expiration_time"
                    ) || "";


                if (
                    !accessToken
                    &&
                    !longLivedToken
                ) {{

                    showError(
                        "Facebook authorization completed, " +
                        "but no access token was returned."
                    );

                    return;
                }}


                const formData =
                    new URLSearchParams();


                if (accessToken) {{

                    formData.append(
                        "access_token",
                        accessToken
                    );

                }}


                if (longLivedToken) {{

                    formData.append(
                        "long_lived_token",
                        longLivedToken
                    );

                }}


                formData.append(
                    "expires_in",
                    expiresIn
                );


                formData.append(
                    "data_access_expiration_time",
                    dataAccessExpirationTime
                );


                const response =
                    await fetch(
                        window.location.pathname,
                        {{
                            method: "POST",

                            credentials:
                                "same-origin",

                            headers: {{

                                "Content-Type":
                                    "application/x-www-form-urlencoded; charset=UTF-8",

                                "X-CSRFToken":
                                    csrfToken,
                            }},

                            body:
                                formData.toString(),
                        }}
                    );


                if (!response.ok) {{

                    const responseText =
                        await response.text();


                    showError(
                        "Facebook connection failed. HTTP " +
                        response.status +
                        ". " +
                        responseText.substring(
                            0,
                            500
                        )
                    );

                    return;
                }}


                const html =
                    await response.text();


                window.history.replaceState(
                    null,
                    document.title,
                    window.location.pathname
                );


                document.open();

                document.write(
                    html
                );

                document.close();


            }} catch (error) {{

                showError(
                    "Facebook connection failed: " +
                    error.message
                );

            }}

        }}


        completeBusinessLogin();

    </script>


</body>

</html>
""",
            content_type='text/html',
        )


    # ========================================================
    # ONLY GET + POST
    # ========================================================

    if request.method != 'POST':

        response = HttpResponse(
            'Only GET and POST requests are supported '
            'for Facebook OAuth callback.',

            status=405,

            content_type='text/plain',
        )


        response['Allow'] = 'GET, POST'


        return response


    # ========================================================
    # VERIFY OAUTH SESSION
    # ========================================================

    oauth_pending = request.session.get(
        'facebook_oauth_pending'
    )


    if not oauth_pending:

        raise ValueError(
            'Facebook OAuth session is missing or expired. '
            'Please start Facebook login again.'
        )


    request.session.pop(
        'facebook_oauth_pending',
        None
    )


    # ========================================================
    # ACCESS TOKEN
    # ========================================================

    access_token = request.POST.get(
        'access_token'
    )


    long_lived_token = request.POST.get(
        'long_lived_token'
    )


    user_access_token = (
        long_lived_token
        or access_token
    )


    if not user_access_token:

        raise ValueError(
            'Facebook user access token was not returned '
            'by the Business Login flow.'
        )


    # ========================================================
    # GET PAGES
    # ========================================================

    pages = (
        _save_facebook_and_instagram_credentials(
            user_access_token,
            request.user,
        )
    )


    # ========================================================
    # RESULT
    # ========================================================

    return render(
        request,
        'facebook_callback.html',
        {
            'pages': pages,

            'capture_token_from_fragment': False,
        }
    )


# ============================================================
# X CALLBACK
# ============================================================


# ============================================================
# X LOGIN
# ============================================================


@login_required(login_url="login")
def x_login(request):

    oauth_data = (
        build_x_authorization_url()
    )

    request.session[
        'x_oauth_state'
    ] = oauth_data[
        'state'
    ]

    request.session[
        'x_code_verifier'
    ] = oauth_data[
        'code_verifier'
    ]

    return redirect(
        oauth_data[
            'authorization_url'
        ]
    )


# ============================================================
# X CALLBACK
# ============================================================


@login_required(login_url="login")
def x_callback(request):

    # --------------------------------------------------------
    # X OAuth error
    # --------------------------------------------------------

    error = request.GET.get(
        'error'
    )

    if error:

        error_description = (
            request.GET.get(
                'error_description',
                error
            )
        )

        return HttpResponse(
            'X OAuth failed: '
            f'{error_description}',
            status=400,
            content_type='text/plain',
        )


    # --------------------------------------------------------
    # Authorization code
    # --------------------------------------------------------

    code = request.GET.get(
        'code'
    )

    state = request.GET.get(
        'state'
    )


    if not code:

        return HttpResponse(
            'X authorization code was not returned.',
            status=400,
            content_type='text/plain',
        )


    # --------------------------------------------------------
    # Verify state
    # --------------------------------------------------------

    saved_state = request.session.get(
        'x_oauth_state'
    )


    if not saved_state:

        return HttpResponse(
            'X OAuth session is missing or expired. '
            'Please start X login again.',
            status=400,
            content_type='text/plain',
        )


    if not state:

        return HttpResponse(
            'X OAuth state was not returned.',
            status=400,
            content_type='text/plain',
        )


    if state != saved_state:

        return HttpResponse(
            'X OAuth state verification failed.',
            status=400,
            content_type='text/plain',
        )


    # --------------------------------------------------------
    # Code verifier
    # --------------------------------------------------------

    code_verifier = request.session.get(
        'x_code_verifier'
    )


    if not code_verifier:

        return HttpResponse(
            'X PKCE code verifier is missing. '
            'Please start X login again.',
            status=400,
            content_type='text/plain',
        )


    # --------------------------------------------------------
    # Remove OAuth session values
    # --------------------------------------------------------

    request.session.pop(
        'x_oauth_state',
        None
    )

    request.session.pop(
        'x_code_verifier',
        None
    )


    # --------------------------------------------------------
    # Exchange code for token
    # --------------------------------------------------------

    try:

        token_data = (
            exchange_code_for_token(
                code,
                code_verifier,
            )
        )


        access_token = (
            token_data.get(
                'access_token'
            )
        )


        if not access_token:

            raise ValueError(
                'X access token was not returned.'
            )


        # ----------------------------------------------------
        # Get X user
        # ----------------------------------------------------

        user_data = (
            get_x_user(
                access_token
            )
        )


        # ----------------------------------------------------
        # Save credential
        # ----------------------------------------------------

        x_user_id = user_data.get("id") or user_data.get("data", {}).get("id")
        existing_x = None

        if x_user_id:
            existing_x = XCredential.objects.filter(
                user_id=x_user_id
            ).first()

        if existing_x is not None and existing_x.owner_id not in (None, request.user.id):
            raise ValueError(
                "This X account is already connected to another user."
            )

        credential = (
            save_x_credential(
                token_data,
                user_data,
            )
        )

        credential.owner = request.user
        credential.save(update_fields=["owner"])


    except Exception as exc:

        print(
            'X OAuth callback failed:'
        )

        print(
            str(exc)
        )

        return HttpResponse(
            'X account connection failed: '
            f'{exc}',
            status=400,
            content_type='text/plain',
        )


    # --------------------------------------------------------
    # Success
    # --------------------------------------------------------

    return HttpResponse(
        'X account connected successfully. '
        f'Username: @{credential.username}',
        content_type='text/plain',
    )
