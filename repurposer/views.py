from pathlib import Path
import hashlib
import hmac
import shutil
import json
import logging
import subprocess
from threading import Thread
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django_ratelimit.decorators import ratelimit

logger = logging.getLogger(__name__)

import requests

from django.shortcuts import (
    render,
    redirect,
    get_object_or_404,
)
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.urls import reverse
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.http import (
    HttpResponse,
    JsonResponse,
)
from django.db.models import Q
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from .forms import AdvertisementUploadForm, VideoUploadForm

from .models import (
    Video,
    YouTubeCredential,
    InstagramCredential,
    FacebookCredential,
    ScheduledPost,
    XCredential,
    UserSettings,
    BackgroundMusic,
    ClipMusicSelection,
    Advertisement,
    SubscriptionPlan,
    SubscriptionPayment,
)
from .subscriptions import (
    account_entitlement, activate_captured_payment, current_subscription, create_razorpay_order,
    razorpay_configured, subscription_allows, verify_razorpay_payment, video_entitlement,
)

from .processing_service import (
    process_video,
)
from .media_access import signed_public_media_url

from .short_compositor import (
    create_vertical_short,
    apply_background_music,
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
from .facebook_analytics_service import get_facebook_dashboard_analytics

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
        form.fields["background_music"].queryset = BackgroundMusic.objects.none()
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
    form.fields["background_music"].queryset = (
        BackgroundMusic.objects.filter(
            Q(user=user) | Q(is_shared=True) | Q(user__isnull=True, is_default=True),
            is_active=True,
        ).distinct().order_by("name")
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

        public_url = signed_public_media_url(clip["path"])
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


def enrich_clips_with_ai_metadata(generated_clips, preferences=None):
    """
    Generate title, description and hashtags for each newly
    generated clip using Gemini.

    If Gemini fails for one clip, keep the clip usable with
    a safe fallback instead of stopping the entire workflow.
    """

    if not generated_clips:
        return generated_clips

    enabled = {
        "title": getattr(preferences, "ai_titles", True),
        "description": (
            getattr(preferences, "ai_descriptions", True)
            or getattr(preferences, "ai_captions", True)
        ),
        "hashtags": getattr(preferences, "ai_hashtags", True),
    }
    # The current metadata generator returns all three fields together.
    # Keep its requests off entirely when the user disabled every AI field.
    should_generate = any(enabled.values())

    enriched_clips = []

    for clip in generated_clips:
        updated_clip = dict(clip)

        try:
            print(
                "Generating AI metadata for clip "
                f"{clip['number']}..."
            )

            metadata = (
                generate_video_file_metadata(clip["file_path"])
                if should_generate
                else {}
            )

            updated_clip["title"] = (
                metadata.get("title")
                if enabled["title"]
                else None
            ) or f"AI Video Repurposer Clip {clip['number']}"
            updated_clip["description"] = (
                metadata.get("description")
                if enabled["description"]
                else None
            ) or "A short-form video created with AI Video Repurposer."
            updated_clip["hashtags"] = (
                metadata.get("hashtags")
                if enabled["hashtags"]
                else None
            ) or ["#Shorts", "#Reels", "#AI", "#Video"]

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
                    len(generated_clips),
                    getattr(
                        getattr(video.user, "settings", None),
                        "timezone",
                        "Asia/Kolkata",
                    ),
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
                    len(generated_clips),
                    getattr(
                        getattr(video.user, "settings", None),
                        "timezone",
                        "Asia/Kolkata",
                    ),
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

        source_relative_path = source_path.relative_to(
            settings.MEDIA_ROOT
        )
        source_media_path = str(source_relative_path).replace("\\", "/")
        selection_path = (
            f"{settings.MEDIA_URL}"
            f"{source_media_path}"
        )
        selection = ClipMusicSelection.objects.filter(
            user=video.user,
            video=video,
            video_path=selection_path,
        ).select_related("background_music").first()

        selected_bgm_path = bgm_path if not selection else None
        if selection and selection.background_music:
            music = selection.background_music
            if (
                music.user_id == video.user_id
                or (music.user_id is None and music.is_default)
            ):
                try:
                    candidate_path = Path(music.audio_file.path)
                    if candidate_path.is_file():
                        selected_bgm_path = candidate_path
                except (AttributeError, NotImplementedError, ValueError):
                    selected_bgm_path = None

        print(
            "Creating vertical 9:16 clip "
            f"{clip['number']}..."
        )

        create_vertical_short(
            input_path=source_path,
            output_path=output_path,
            bgm_path=(
                selected_bgm_path
                if selected_bgm_path and Path(selected_bgm_path).is_file()
                else None
            ),
        )

        # A new processing pass may have changed the selected track. The
        # vertical output is current now, so discard any older saved render.
        music_output = (
            Path(settings.MEDIA_ROOT)
            / "processed"
            / "music"
            / str(video.id)
            / f"clip_{clip['number']}.mp4"
        )
        music_output.unlink(missing_ok=True)

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

        # Upload-time music is applied only after the source clips exist,
        # so generation retains its existing no-music behavior by default.
        if video.background_music_id:
            generated_clips = get_generated_clips(video)
            for clip in generated_clips:
                render_clip_background_music(video, clip, video.background_music)
            generated_clips = get_generated_clips(video)


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
            generated_clips,
            getattr(video.user, "settings", None),
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

        logger.exception(
            "Background video processing failed."
        )


        try:

            video = Video.objects.get(
                id=video_id
            )

            video.status = 'failed'

            video.processing_progress = 100

            video.processing_stage = (
                'Processing failed. Please try again.'
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

    if vertical_clip_files and video.output_format == "vertical":
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
    selections = {
        selection.video_path: selection
        for selection in ClipMusicSelection.objects.filter(
            user=video.user,
            video=video,
        ).select_related("background_music")
    }

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

        source_name = (
            f"normal_clip_{clip_number(clip_file)}.mp4"
            if video.processing_mode == "normal"
            else f"clip_{clip_number(clip_file)}.mp4"
        )
        source_file = output_directory / source_name
        source_relative_path = source_file.relative_to(settings.MEDIA_ROOT)
        source_media_path = str(source_relative_path).replace("\\", "/")
        selection_path = (
            f"{settings.MEDIA_URL}"
            f"{source_media_path}"
        )

        clip = {
            "number": index,
            "path": (
                f"{settings.MEDIA_URL}{media_path}"
            ),
            "file_path": str(clip_file),
            "source_file_path": str(source_file),
            "selection_path": selection_path,
            "duration": "â€”",
            "text": (
                "Normal sequential cut"
                if video.processing_mode == "normal"
                else "Viral clip"
            ),
            "score": "â€”",
        }

        selection = selections.get(selection_path)
        if selection:
            music_output = (
                Path(settings.MEDIA_ROOT)
                / "processed"
                / "music"
                / str(video.id)
                / f"clip_{clip_number(clip_file)}.mp4"
            )
            if music_output.is_file():
                relative_path = music_output.relative_to(settings.MEDIA_ROOT)
                media_path = str(relative_path).replace("\\", "/")
                clip["file_path"] = str(music_output)
                clip["path"] = f"{settings.MEDIA_URL}{media_path}"
            clip["selected_music_id"] = selection.background_music_id
        else:
            clip["selected_music_id"] = None

        generated_clips.append(clip)

    return generated_clips


def render_clip_background_music(video, clip, music):
    """Render and persist a clip's chosen shared or private music (or silence)."""
    source_path = Path(clip["source_file_path"])
    if not source_path.is_file():
        raise FileNotFoundError(f"Generated clip file is missing: {source_path}")

    music_path = None
    if music is not None:
        music_path = Path(music.audio_file.path)
        if not music_path.is_file():
            raise FileNotFoundError(f"Background music file is missing: {music_path}")

    vertical_source = (
        Path(settings.MEDIA_ROOT) / "processed" / "vertical" / str(video.id)
        / f"vertical_clip_{clip['number']}.mp4"
    )
    output_path = (
        Path(settings.MEDIA_ROOT) / "processed" / "music" / str(video.id)
        / f"clip_{clip['number']}.mp4"
    )
    temp_output = output_path.with_name(f"{output_path.stem}.tmp{output_path.suffix}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if video.output_format == "vertical" and vertical_source.is_file():
        create_vertical_short(source_path, temp_output, bgm_path=music_path)
    elif music_path:
        apply_background_music(source_path, temp_output, bgm_path=music_path)
    else:
        shutil.copy2(source_path, temp_output)
    temp_output.replace(output_path)

    ClipMusicSelection.objects.update_or_create(
        user=video.user,
        video=video,
        video_path=clip["selection_path"],
        defaults={"background_music": music},
    )
    return output_path


# ============================================================
# HOME / DASHBOARD
# ============================================================


@ratelimit(key="user_or_ip", rate="120/m", method="GET", block=True)
def home(request):
    """Lightweight dashboard; processing and list pages have their own routes."""
    user = request.user
    user_settings = (
        UserSettings.objects.get_or_create(user=user)[0]
        if user.is_authenticated else None
    )
    form_initial = {}
    if user_settings:
        form_initial = {
            "output_format": user_settings.default_output_format,
            "processing_mode": user_settings.default_processing_mode,
        }

    if request.method == "POST" and not user.is_authenticated:
        return redirect(f"{reverse('login')}?next={quote(request.get_full_path(), safe='')}")

    form = _configure_video_form_for_user(VideoUploadForm(initial=form_initial), user)
    if request.method == "POST":
        allowed, subscription, used, remaining = video_entitlement(user)
        if not allowed:
            messages.error(request, "Your free video allowance or subscription has expired. Choose a paid plan to continue processing videos.")
            return redirect("subscription_page")
        form = _configure_video_form_for_user(
            VideoUploadForm(request.POST, request.FILES, initial=form_initial), user
        )
        if not form.is_valid():
            return render(request, "workspace/upload_video.html", {
                "form": form,
                "background_music": _available_background_music_for_user(user),
                "trending_metadata": _clean_youtube_source_metadata(
                    request.POST.get("source_metadata", "")
                ),
                "trending_metadata_json": json.dumps(
                    _clean_youtube_source_metadata(
                        request.POST.get("source_metadata", "")
                    ),
                    ensure_ascii=False,
                ),
                "active_page": "upload",
                "page_title": "Upload Video",
                "video_entitlement": (subscription, used, remaining),
                "video_allowed": allowed,
                "video_usage_count": used,
                "video_remaining_count": remaining,
                "video_usage_limit": subscription.plan.video_limit if subscription and subscription.plan else None,
            })

        video = form.save(commit=False)
        video.user = user
        video.source_metadata = _clean_youtube_source_metadata(
            request.POST.get("source_metadata", "")
        )
        workflow_mode = form.cleaned_data.get("workflow_mode", "auto")
        auto_platforms = form.cleaned_data.get("auto_platforms") or []
        video.workflow_mode = workflow_mode
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
        video.status = "processing"
        video.processing_progress = 0
        video.processing_stage = "Starting processing"
        video.save()

        if settings.CELERY_BROKER_URL:
            from .tasks import process_video_task
            process_video_task.delay(video.id)
        else:
            Thread(
                target=background_process_video,
                args=(video.id,),
                daemon=True,
            ).start()
        messages.success(
            request,
            f"Video #{video.id} received. Background processing has started.",
        )
        return redirect(f"/?video_id={video.id}")

    videos = Video.objects.filter(user=user) if user.is_authenticated else Video.objects.none()
    video_id = request.GET.get("video_id")
    video = None
    if video_id and user.is_authenticated:
        video = videos.filter(pk=video_id).first()
    if video is None and user.is_authenticated:
        video = videos.filter(status="completed").order_by("-updated_at").first()

    generated_clips = get_generated_clips(video) if video and video.status == "completed" else []
    entitlement = video_entitlement(user) if user.is_authenticated else (False, None, None, None)
    upcoming = (
        ScheduledPost.objects.filter(
            user=user, status="scheduled", scheduled_at__gt=timezone.now()
        ).select_related("video").order_by("scheduled_at")[:5]
        if user.is_authenticated else ScheduledPost.objects.none()
    )
    context = {
        "form": form,
        "video": video,
        "generated_clips": generated_clips,
        "advertisements": Advertisement.objects.filter(is_active=True, placement="dashboard_top").order_by("-created_at")[:8],
        "bottom_advertisements": Advertisement.objects.filter(is_active=True, placement="dashboard_bottom").order_by("-created_at")[:8],
        "advertisement_form": AdvertisementUploadForm() if user.is_authenticated and user.is_staff else None,
        "recent_videos": videos.order_by("-created_at")[:6],
        "processing_videos": videos.filter(status="processing").count(),
        "completed_videos": videos.filter(status="completed").count(),
        "failed_videos": videos.filter(status="failed").count(),
        "scheduled_posts": upcoming.count(),
        "recent_scheduled_posts": upcoming,
        "published_posts": ScheduledPost.objects.filter(
            user=user, status="published"
        ).count() if user.is_authenticated else 0,
        "active_page": "dashboard",
        "page_title": "Dashboard",
        "profile_picture": user_settings.profile_picture if user_settings else None,
        "video_entitlement": entitlement[1:],
        "video_allowed": entitlement[0],
        "video_usage_count": entitlement[2],
        "video_remaining_count": entitlement[3],
        "video_usage_limit": entitlement[1].plan.video_limit if entitlement[1] and entitlement[1].plan else None,
    }
    return render(request, "workspace/dashboard.html", context)


@login_required(login_url="login")
def upload_video_page(request):
    user_settings = UserSettings.objects.get_or_create(user=request.user)[0]
    form = VideoUploadForm(initial={
        "output_format": user_settings.default_output_format,
        "processing_mode": user_settings.default_processing_mode,
    })
    form = _configure_video_form_for_user(form, request.user)
    trending_metadata = _clean_youtube_source_metadata({
        "video_id": request.GET.get("source_video_id", ""),
        "title": request.GET.get("source_title", ""),
        "channel_title": request.GET.get("source_channel_title", ""),
        "channel_id": request.GET.get("source_channel_id", ""),
        "thumbnail": request.GET.get("source_thumbnail", ""),
        "published_at": request.GET.get("source_published_at", ""),
        "description": request.GET.get("source_description", ""),
        "category_id": request.GET.get("source_category_id", ""),
        "tags": request.GET.get("source_tags", "[]"),
    })
    youtube_url = request.GET.get("youtube_url", "").strip()
    if youtube_url:
        form.initial["youtube_url"] = youtube_url
    entitlement = video_entitlement(request.user)
    return render(request, "workspace/upload_video.html", {
        "form": form,
        "background_music": _available_background_music_for_user(request.user),
        "trending_metadata": trending_metadata,
        "trending_metadata_json": json.dumps(trending_metadata, ensure_ascii=False),
        "active_page": "upload",
        "page_title": "Upload Video",
        "video_entitlement": entitlement[1:],
        "video_allowed": entitlement[0],
        "video_usage_count": entitlement[2],
        "video_remaining_count": entitlement[3],
        "video_usage_limit": entitlement[1].plan.video_limit if entitlement[1] and entitlement[1].plan else None,
    })


@login_required(login_url="login")
def subscription_page(request):
    subscription = current_subscription(request.user)
    allowed, subscription, used, remaining = video_entitlement(request.user)
    limit = subscription.plan.video_limit if subscription and subscription.plan else None
    accounts = {
        "YouTube": YouTubeCredential.objects.filter(user=request.user, is_active=True).count(),
        "Instagram": InstagramCredential.objects.filter(user=request.user, is_active=True).count(),
        "Facebook": FacebookCredential.objects.filter(user=request.user, is_active=True).count(),
        "X": XCredential.objects.filter(owner=request.user, is_active=True).count(),
    }
    return render(request, "workspace/subscription.html", {
        "active_page": "subscription", "page_title": "Subscription & Billing",
        "subscription": subscription, "can_process": allowed, "videos_used": used,
        "videos_remaining": remaining, "video_limit": limit, "account_usage": accounts,
        "plans": SubscriptionPlan.objects.filter(is_active=True).order_by("sort_order", "price"),
        "payments": SubscriptionPayment.objects.filter(user=request.user).select_related("plan")[:30],
        "payment_configured": razorpay_configured(),
        "razorpay_key_id": getattr(settings, "RAZORPAY_KEY_ID", ""),
        "razorpay_mode": getattr(settings, "RAZORPAY_MODE", "test"),
        "days_remaining": max(0, (subscription.expires_at.date() - timezone.localdate()).days) if subscription and subscription.expires_at else None,
    })


@login_required(login_url="login")
@require_POST
def subscription_create_order(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, pk=plan_id, is_active=True, is_trial=False)
    try:
        order = create_razorpay_order(request.user, plan)
    except (RuntimeError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except requests.RequestException:
        logger.exception("Could not create Razorpay order for user %s", request.user.pk)
        return JsonResponse({"error": "Payment service could not create an order. Please try again later."}, status=502)
    return JsonResponse({"order_id": order["id"], "amount": order["amount"], "currency": order["currency"], "key": settings.RAZORPAY_KEY_ID, "name": "AI Video Repurposer", "description": plan.name})


@login_required(login_url="login")
@require_POST
def subscription_verify_payment(request):
    try:
        payload = json.loads(request.body or "{}")
        payment = verify_razorpay_payment(request.user, payload.get("razorpay_order_id", ""), payload.get("razorpay_payment_id", ""), payload.get("razorpay_signature", ""))
    except (json.JSONDecodeError, KeyError, ValueError, SubscriptionPayment.DoesNotExist) as exc:
        return JsonResponse({"error": str(exc) or "Payment could not be verified."}, status=400)
    except requests.RequestException:
        logger.exception("Razorpay verification lookup failed for user %s", request.user.pk)
        return JsonResponse({"error": "Payment is still being verified. Refresh this page in a moment."}, status=502)
    if payment.status != "captured":
        return JsonResponse({"error": "Payment is not captured yet. Your plan has not been activated."}, status=400)
    return JsonResponse({"success": True})


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
    signature = request.headers.get("X-Razorpay-Signature", "")
    expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest() if secret else ""
    if not secret or not hmac.compare_digest(expected, signature):
        return HttpResponse("Invalid webhook signature", status=400)
    try:
        payload = json.loads(request.body)
        if payload.get("event") != "payment.captured":
            return JsonResponse({"received": True})
        data = payload["payload"]["payment"]["entity"]
        payment = SubscriptionPayment.objects.select_related("plan", "user").get(order_id=data.get("order_id"))
        if (data.get("status") != "captured" or data.get("amount") != int(payment.amount * 100)
                or data.get("currency") != payment.currency or not data.get("id")):
            return HttpResponse("Payment details do not match order", status=400)
        data["source"] = "webhook"
        activate_captured_payment(payment, data["id"], data)
    except (ValueError, KeyError, TypeError, SubscriptionPayment.DoesNotExist) as exc:
        return HttpResponse(str(exc) or "Invalid webhook payload", status=400)
    return JsonResponse({"received": True})


@login_required(login_url="login")
def videos_page(request):
    return render(request, "workspace/videos.html", {
        "videos": Video.objects.filter(user=request.user).order_by("-created_at"),
        "active_page": "videos",
        "page_title": "Videos",
    })


@login_required(login_url="login")
def upcoming_schedule_page(request):
    posts = ScheduledPost.objects.filter(
        user=request.user, status="scheduled", scheduled_at__gt=timezone.now()
    ).select_related("video", "youtube_channel", "instagram_account", "facebook_page", "x_account").order_by("scheduled_at")
    return render(request, "workspace/upcoming.html", {
        "posts": posts,
        "active_page": "schedule",
        "page_title": "Upcoming Schedule",
    })


@login_required(login_url="login")
def generated_clips_page(request):
    videos = Video.objects.filter(user=request.user, status="completed").order_by("-updated_at")[:30]
    clip_groups = []
    for video in videos:
        video_clips = get_generated_clips(video)
        for clip in video_clips:
            clip["video_id"] = video.id
            clip["video_title"] = f"Video #{video.id}"
        if video_clips:
            clip_groups.append({"video_id": video.id, "title": f"Video #{video.id}", "clips": video_clips})
    return render(request, "workspace/clips.html", {
        "clip_groups": clip_groups,
        "background_music": _available_background_music_for_user(request.user),
        "youtube_channels": YouTubeCredential.objects.filter(user=request.user, is_active=True),
        "instagram_accounts": InstagramCredential.objects.filter(user=request.user, is_active=True),
        "facebook_pages": FacebookCredential.objects.filter(user=request.user, is_active=True),
        "x_accounts": XCredential.objects.filter(owner=request.user, is_active=True),
        "active_page": "clips",
        "page_title": "Generated Clips",
    })


@login_required(login_url="login")
def failed_items_page(request):
    return render(request, "workspace/failed.html", {
        "videos": Video.objects.filter(user=request.user, status="failed").order_by("-updated_at"),
        "posts": ScheduledPost.objects.filter(user=request.user, status="failed").select_related("video").order_by("-updated_at"),
        "active_page": "failed",
        "page_title": "Failed Items",
    })


@login_required(login_url="login")
def analytics_page(request):
    user_posts = ScheduledPost.objects.filter(user=request.user)
    return render(request, "workspace/analytics.html", {
        "total_videos": Video.objects.filter(user=request.user).count(),
        "completed_videos": Video.objects.filter(user=request.user, status="completed").count(),
        "published_posts": user_posts.filter(status="published").count(),
        "scheduled_posts": user_posts.filter(status="scheduled").count(),
        "failed_posts": user_posts.filter(status="failed").count(),
        "platform_counts": {
            platform: user_posts.filter(platform=platform, status="published").count()
            for platform, _label in ScheduledPost.PLATFORM_CHOICES
        },
        "active_page": "analytics",
        "page_title": "Analytics",
    })


@login_required(login_url="login")
def workspace_page(request):
    """A focused workspace landing page for the sidebar Workspace entry."""
    user_videos = Video.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "workspace/workspace.html", {
        "videos": user_videos[:20],
        "total_videos": user_videos.count(),
        "processing_videos": user_videos.filter(status="processing").count(),
        "completed_videos": user_videos.filter(status="completed").count(),
        "failed_videos": user_videos.filter(status="failed").count(),
        "active_page": "workspace",
        "page_title": "Workspace",
    })


@login_required(login_url="login")
def progress_report_page(request):
    """Detailed publishing progress using the same per-user data as Analytics."""
    user_posts = ScheduledPost.objects.filter(user=request.user)
    youtube_data = get_youtube_dashboard_analytics(
        YouTubeCredential.objects.filter(user=request.user, is_active=True)
    )
    facebook_data = get_facebook_dashboard_analytics(
        FacebookCredential.objects.filter(user=request.user, is_active=True)
    )
    available_youtube = [a for a in youtube_data.get("accounts", []) if a.get("available")]
    available_facebook = [a for a in facebook_data.get("accounts", []) if a.get("available")]
    views_values = [a.get("total_channel_views") for a in available_youtube if a.get("total_channel_views") is not None]
    follower_values = [
        value for value in (
            *(a.get("subscribers") for a in available_youtube),
            *(a.get("followers") for a in available_facebook),
        ) if value is not None
    ]
    return render(request, "workspace/progress_report.html", {
        "total_videos": Video.objects.filter(user=request.user).count(),
        "completed_videos": Video.objects.filter(user=request.user, status="completed").count(),
        "published_posts": user_posts.filter(status="published").count(),
        "scheduled_posts": user_posts.filter(status="scheduled").count(),
        "failed_posts": user_posts.filter(status="failed").count(),
        "total_channel_views": sum(views_values) if views_values else None,
        "total_followers": sum(follower_values) if follower_values else None,
        "views_source_available": bool(views_values),
        "followers_source_available": bool(follower_values),
        "active_page": "progress",
        "page_title": "Progress Report",
    })


@login_required(login_url="login")
def youtube_trending_page(request):
    """Show YouTube's live most-popular videos for the selected region."""
    region_code = (request.GET.get("region", "IN") or "IN").strip().upper()
    if not (len(region_code) == 2 and region_code.isalpha()):
        region_code = "IN"
    trend_data = get_youtube_trend_context(
        api_key=settings.YOUTUBE_API_KEY,
        region_code=region_code,
        max_results=25,
    )
    for video in trend_data.get("videos", []):
        video["tags_json"] = json.dumps(video.get("tags", []), ensure_ascii=False)
    return render(request, "workspace/youtube_trending.html", {
        "trend_data": trend_data,
        "region_code": region_code,
        "active_page": "trending",
        "page_title": "YouTube Trending",
    })


def _clean_youtube_source_metadata(raw_metadata):
    """Allow only bounded metadata from the public trends API."""
    allowed_lengths = {
        "video_id": 80,
        "title": 500,
        "channel_title": 300,
        "channel_id": 150,
        "thumbnail": 2000,
        "published_at": 80,
        "description": 8000,
        "category_id": 40,
    }
    try:
        metadata = raw_metadata
        if isinstance(raw_metadata, str):
            if len(raw_metadata) > 16000:
                return {}
            metadata = json.loads(raw_metadata)
        if not isinstance(metadata, dict):
            return {}
        cleaned = {
            key: str(metadata.get(key, ""))[:max_length]
            for key, max_length in allowed_lengths.items()
            if metadata.get(key) is not None
        }
        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (TypeError, json.JSONDecodeError):
                tags = tags.split("||") if tags else []
        if isinstance(tags, list):
            cleaned["tags"] = [str(tag)[:200] for tag in tags[:50]]
        return cleaned
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


@login_required(login_url="login")
def social_accounts_page(request):
    youtube_channels = YouTubeCredential.objects.filter(user=request.user, is_active=True)
    instagram_accounts = InstagramCredential.objects.filter(user=request.user, is_active=True)
    facebook_pages = FacebookCredential.objects.filter(user=request.user, is_active=True)
    x_accounts = XCredential.objects.filter(owner=request.user, is_active=True)
    return render(request, "workspace/accounts.html", {
        "youtube_channels": youtube_channels,
        "instagram_accounts": instagram_accounts,
        "facebook_pages": facebook_pages,
        "x_accounts": x_accounts,
        "connected_account_count": youtube_channels.count() + instagram_accounts.count() + facebook_pages.count() + x_accounts.count(),
        "active_page": "accounts",
        "page_title": "Social Accounts",
    })


@login_required(login_url="login")
@user_passes_test(lambda user: user.is_staff, login_url="home")
@require_POST
def advertisement_upload(request):
    form = AdvertisementUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Ad upload failed: " + "; ".join(
            error for errors in form.errors.values() for error in errors
        ))
        return redirect("home")
    Advertisement.objects.create(
        media_file=form.cleaned_data["media_file"],
        destination_url=form.cleaned_data["destination_url"],
        is_active=True,
    )
    messages.success(request, "Advertisement is now active on the dashboard.")
    return redirect("home")


@login_required(login_url="login")
@require_POST
def delete_video(request, video_id):
    video = get_object_or_404(Video, pk=video_id, user=request.user)
    if video.status == "processing":
        messages.error(request, "A video cannot be deleted while it is processing.")
        return redirect("videos_page")

    media_root = Path(settings.MEDIA_ROOT).resolve()
    if video.video_file:
        video.video_file.delete(save=False)
    for folder in ("viral", "normal", "vertical", "music"):
        target = (media_root / "processed" / folder / str(video.id)).resolve()
        if media_root in target.parents and target.is_dir():
            shutil.rmtree(target)
    video.delete()
    messages.success(request, "Video and generated clips deleted.")
    return redirect("videos_page")


# ============================================================
# MANUAL PUBLISH
# ============================================================


@login_required(login_url="login")
@ratelimit(key="user_or_ip", rate="10/h", method="POST", block=True)
def manual_publish(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POST required."},
            status=405,
        )

    if not subscription_allows(request.user, "publishing"):
        return JsonResponse({"success": False, "error": "Your plan does not include active publishing. Renew or upgrade to continue."}, status=402)

    try:
        video = get_object_or_404(
            Video,
            id=request.POST.get("video_id"),
            user=request.user,
        )
        if video.status != "completed":
            raise ValueError("Video processing is not completed yet.")

        clips, platforms, accounts = _manual_selection(request, video)
        unavailable = [platform for platform in platforms if not subscription_allows(request.user, platform)]
        if unavailable:
            raise ValueError(f"Your current plan does not include {', '.join(unavailable)} publishing.")
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
                    logger.exception(
                        "Manual publish failed for clip %s on %s.",
                        clip["number"],
                        platform,
                    )
                    results.append({
                        "clip_number": clip["number"],
                        "platform": platform,
                        "status": "failed",
                        "error": "Publishing failed. Please try again later.",
                    })

        return JsonResponse({
            "success": True,
            "results": results,
        })

    except Exception as exc:
        logger.exception("Manual publish request failed.")
        return JsonResponse({
            "success": False,
            "error": "Publishing request failed. Please try again later.",
        }, status=400)


# ============================================================
# MANUAL SCHEDULE
# ============================================================


@login_required(login_url="login")
@ratelimit(key="user_or_ip", rate="20/h", method="POST", block=True)
def manual_schedule(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POST required."},
            status=405,
        )

    if not subscription_allows(request.user, "scheduling"):
        return JsonResponse({"success": False, "error": "Your plan does not include active scheduling. Renew or upgrade to continue."}, status=402)

    try:
        video = get_object_or_404(
            Video,
            id=request.POST.get("video_id"),
            user=request.user,
        )
        if video.status != "completed":
            raise ValueError("Video processing is not completed yet.")

        clips, platforms, accounts = _manual_selection(request, video)
        unavailable = [platform for platform in platforms if not subscription_allows(request.user, platform)]
        if unavailable:
            raise ValueError(f"Your current plan does not include {', '.join(unavailable)} publishing.")
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

        selected_music_id = request.POST.get("background_music_id", "saved").strip()
        if selected_music_id.lower() not in {"saved", "keep", ""}:
            schedule_music = None
            if selected_music_id.lower() not in {"none", "0", "null"}:
                try:
                    selected_music_pk = int(selected_music_id)
                except ValueError as exc:
                    raise ValueError("Invalid background music selection.") from exc
                schedule_music = BackgroundMusic.objects.filter(
                    Q(id=selected_music_pk, user=request.user)
                    | Q(id=selected_music_pk, is_shared=True)
                    | Q(id=selected_music_pk, user__isnull=True, is_default=True),
                    is_active=True,
                ).first()
                if schedule_music is None:
                    raise ValueError("That background music is not available.")

            for clip in clips:
                rendered_path = render_clip_background_music(video, clip, schedule_music)
                clip["file_path"] = str(rendered_path)
                clip["path"] = f"{settings.MEDIA_URL}{rendered_path.relative_to(settings.MEDIA_ROOT).as_posix()}"

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
        logger.exception("Manual schedule request failed.")
        return JsonResponse({
            "success": False,
            "error": "Scheduling request failed. Please try again later.",
        }, status=400)


# ============================================================
# PROCESSING STATUS API
# ============================================================

def ratelimited_error(request, exception):
    return JsonResponse(
        {
            "success": False,
            "error": "Too many requests. Please try again later.",
        },
        status=429,
    )
@login_required(login_url="login")
@ratelimit(key="user_or_ip", rate="120/m", method="GET", block=True)
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
@ratelimit(key="user_or_ip", rate="20/h", method="GET", block=True)
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

    allowed, reason = account_entitlement(
        request.user, "youtube", existing=bool(youtube_credential and youtube_credential.user_id == request.user.id)
    )
    if not allowed:
        messages.error(request, reason)
        return redirect("subscription_page")

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
@ratelimit(key="user_or_ip", rate="10/h", method="GET", block=True)
def instagram_login(request):

    authorization_url = (
        get_instagram_authorization_url()
    )


    return redirect(
        authorization_url
    )


@login_required(login_url="login")
@ratelimit(key="user_or_ip", rate="20/h", method="GET", block=True)
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

    allowed, reason = account_entitlement(
        request.user, "instagram", existing=bool(existing_instagram and existing_instagram.user_id == request.user.id)
    )
    if not allowed:
        messages.error(request, reason)
        return redirect("subscription_page")

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
@ratelimit(key="user_or_ip", rate="10/h", method="GET", block=True)
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

        allowed, _ = account_entitlement(
            owner, "facebook", existing=bool(existing_facebook and existing_facebook.user_id == owner.id)
        )
        if not allowed:
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

            allowed, _ = account_entitlement(
                owner, "instagram", existing=bool(existing_instagram and existing_instagram.user_id == owner.id)
            )
            if not allowed:
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
@ratelimit(key="user_or_ip", rate="20/h", method="GET", block=True)
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
@ratelimit(key="user_or_ip", rate="10/h", method="GET", block=True)
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
@ratelimit(key="user_or_ip", rate="20/h", method="GET", block=True)
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

        allowed, reason = account_entitlement(
            request.user, "x", existing=bool(existing_x and existing_x.owner_id == request.user.id)
        )
        if not allowed:
            raise ValueError(reason)

        credential = (
            save_x_credential(
                token_data,
                user_data,
            )
        )

        credential.owner = request.user
        credential.save(update_fields=["owner"])


    except Exception as exc:

        logger.exception(
            "X OAuth callback failed."
        )

        return HttpResponse(
            'X account connection failed. Please try again.',
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

# ============================================================
# Settings / Background Music
# ============================================================

# ============================================================
# BACKGROUND MUSIC
# ============================================================

ALLOWED_BACKGROUND_MUSIC_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".flac",
}
MAX_BACKGROUND_MUSIC_SIZE_BYTES = 25 * 1024 * 1024


def _available_background_music_for_user(user):
    """
    Return music owned by the user, shared by any uploader, or app defaults.
    """
    return BackgroundMusic.objects.filter(
        Q(user=user) | Q(is_shared=True) | Q(user__isnull=True, is_default=True),
        is_active=True,
    ).order_by(
        "-is_shared",
        "-is_default",
        "-created_at",
    ).distinct()


@login_required(login_url="login")
@require_POST
def background_music_upload(request):
    """
    Upload a track owned by the current user and shared across the app.
    """
    if request.method != "POST":
        return redirect("home")

    music_file = request.FILES.get("audio_file")
    music_name = request.POST.get("name", "").strip()

    if not music_file or music_file.size <= 0:
        messages.error(request, "Choose a non-empty audio file.")
        return redirect("upload_video_page")

    extension = Path(music_file.name).suffix.lower()

    if (
        extension not in ALLOWED_BACKGROUND_MUSIC_EXTENSIONS
        or music_file.size > MAX_BACKGROUND_MUSIC_SIZE_BYTES
    ):
        messages.error(request, "Use a supported audio file under 25 MB.")
        return redirect("upload_video_page")

    if not music_name:
        music_name = Path(music_file.name).stem[:200]

    music = BackgroundMusic.objects.create(
        user=request.user,
        name=music_name[:200],
        audio_file=music_file,
        is_default=False,
        is_shared=True,
    )

    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=index", "-of", "csv=p=0",
                music.audio_file.path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        valid_audio = probe.returncode == 0 and bool(probe.stdout.strip())
    except OSError:
        valid_audio = False

    if not valid_audio:
        music.audio_file.delete(save=False)
        music.delete()
        messages.error(request, "The uploaded file does not contain valid audio.")
        return redirect("upload_video_page")

    messages.success(request, "Music uploaded and shared with all users.")
    return redirect("upload_video_page")


@login_required(login_url="login")
@require_POST
def background_music_delete(request, music_id):
    """
    Delete only music owned by the current user.

    Global/default application music can never be deleted by a normal
    user through this endpoint.
    """
    if request.method != "POST":
        return redirect("home")

    music = get_object_or_404(
        BackgroundMusic,
        id=music_id,
        user=request.user,
    )

    if music.audio_file:
        try:
            music.audio_file.delete(save=False)
        except Exception as exc:
            logger.warning(
                "Failed to remove background music file %s: %s",
                music.id,
                exc,
            )

    music.delete()

    messages.success(request, "Background music deleted.")
    return redirect("upload_video_page")


@login_required(login_url="login")
def save_clip_music_selection(request):
    """
    Save the background music selection for one generated clip.

    Selection can be:
      - empty / 0 / "none" => No Music
      - a user-owned music item
      - an application-wide default music item

    The video and music ownership are both validated server-side.
    """
    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "error": "POST request required.",
            },
            status=405,
        )

    video_id = request.POST.get("video_id", "").strip()
    video_path = request.POST.get("video_path", "").strip()
    music_id = request.POST.get("background_music_id", "").strip()

    if not video_id or not video_path:
        return JsonResponse(
            {
                "success": False,
                "error": "Video and clip path are required.",
            },
            status=400,
        )

    try:
        video_id = int(video_id)
    except (TypeError, ValueError):
        return JsonResponse(
            {
                "success": False,
                "error": "Invalid video.",
            },
            status=400,
        )

    video = get_object_or_404(
        Video,
        id=video_id,
        user=request.user,
    )

    generated_clips = get_generated_clips(video)

    requested_path = Path(video_path)

    try:
        requested_path = requested_path.resolve()
    except OSError:
        requested_path = Path(video_path)

    clip = next(
        (
            item for item in generated_clips
            if item.get("source_file_path")
            and Path(item["source_file_path"]).resolve() == requested_path
        ),
        None,
    )

    if not clip:
        return JsonResponse(
            {
                "success": False,
                "error": "Invalid clip for this video.",
            },
            status=400,
        )

    selected_music = None

    if music_id and music_id.lower() not in {
        "0",
        "none",
        "null",
    }:
        try:
            music_id_int = int(music_id)
        except (TypeError, ValueError):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Invalid background music.",
                },
                status=400,
            )

        selected_music = (
            BackgroundMusic.objects.filter(
                Q(
                    id=music_id_int,
                    user=request.user,
                )
                | Q(
                    id=music_id_int,
                    is_shared=True,
                )
                | Q(
                    id=music_id_int,
                    user__isnull=True,
                    is_default=True,
                ),
                is_active=True,
            )
            .first()
        )

        if not selected_music:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Background music is not available.",
                },
                status=403,
            )

    try:
        output_path = render_clip_background_music(video, clip, selected_music)
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        logger.exception("Could not render background music for clip %s.", clip["number"])
        return JsonResponse(
            {"success": False, "error": "Could not apply background music to this clip."},
            status=500,
        )

    selection = ClipMusicSelection.objects.get(
        user=request.user,
        video=video,
        video_path=clip["selection_path"],
    )

    return JsonResponse(
        {
            "success": True,
            "selection_id": selection.id,
            "music_id": (
                selected_music.id
                if selected_music
                else None
            ),
            "music_name": (
                selected_music.name
                if selected_music
                else "No Music"
            ),
            "clip_path": (
                f"{settings.MEDIA_URL}"
                f"{output_path.relative_to(settings.MEDIA_ROOT).as_posix()}"
            ),
        }
    )


@login_required(login_url="login")
def settings_view(request):
    current_user = request.user

    # Get or create settings for the current user
    user_settings, created = UserSettings.objects.get_or_create(
        user=current_user
    )

    profile_error = ""

    if request.method == "POST":
        first_name = request.POST.get(
            "first_name",
            "",
        ).strip()

        last_name = request.POST.get(
            "last_name",
            "",
        ).strip()

        current_user.first_name = first_name
        current_user.last_name = last_name

        current_user.save(
            update_fields=[
                "first_name",
                "last_name",
            ]
        )

        profile_picture = request.FILES.get("profile_picture")
        if profile_picture:
            extension = Path(profile_picture.name).suffix.lower()
            try:
                profile_picture.seek(0)
                header = profile_picture.read(12)
                profile_picture.seek(0)
            except OSError:
                header = b""
            is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
            is_jpeg = header.startswith(b"\xff\xd8\xff")
            is_gif = header.startswith((b"GIF87a", b"GIF89a"))
            is_webp = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
            if extension not in {".png", ".jpg", ".jpeg", ".gif", ".webp"} or not (
                is_png or is_jpeg or is_gif or is_webp
            ):
                profile_error = "Profile photo must be a valid PNG, JPEG, GIF or WebP image."
            else:
                if user_settings.profile_picture:
                    user_settings.profile_picture.delete(save=False)
                user_settings.profile_picture = profile_picture
                user_settings.save(update_fields=["profile_picture", "updated_at"])

        # ================================================
        # Video & Publishing Preferences
        # ================================================

        default_output_format = request.POST.get(
            "default_output_format",
            user_settings.default_output_format,
        )

        default_processing_mode = request.POST.get(
            "default_processing_mode",
            user_settings.default_processing_mode,
        )

        auto_publish = request.POST.get(
            "auto_publish"
        ) == "on"

        if default_output_format not in {"vertical", "horizontal"}:
            default_output_format = user_settings.default_output_format
        if default_processing_mode not in {"viral", "normal"}:
            default_processing_mode = user_settings.default_processing_mode

        user_settings.default_output_format = (
            default_output_format
        )

        user_settings.default_processing_mode = (
            default_processing_mode
        )

        user_settings.auto_publish = auto_publish

        # ================================================
        # Schedule Preferences
        # ================================================

        timezone_value = request.POST.get(
            "timezone",
            user_settings.timezone,
        ).strip()

        if timezone_value:
            try:
                ZoneInfo(timezone_value)
            except (ZoneInfoNotFoundError, ValueError):
                timezone_value = user_settings.timezone
            user_settings.timezone = timezone_value

        # ================================================
        # AI Preferences
        # ================================================

        user_settings.ai_captions = (
            request.POST.get("ai_captions") == "on"
        )

        user_settings.ai_titles = (
            request.POST.get("ai_titles") == "on"
        )

        user_settings.ai_descriptions = (
            request.POST.get("ai_descriptions") == "on"
        )

        user_settings.ai_hashtags = (
            request.POST.get("ai_hashtags") == "on"
        )

        # ================================================
        # Notification Preferences
        # ================================================

        user_settings.processing_notifications = (
            request.POST.get("processing_notifications") == "on"
        )

        user_settings.publishing_notifications = (
            request.POST.get("publishing_notifications") == "on"
        )

        user_settings.account_notifications = (
            request.POST.get("account_notifications") == "on"
        )

        user_settings.save()

        settings_saved = True

    else:
        settings_saved = False

    # ================================================
    # Connected Accounts
    # ================================================

    youtube_accounts = YouTubeCredential.objects.filter(
        user=current_user,
        is_active=True,
    )

    instagram_accounts = InstagramCredential.objects.filter(
        user=current_user,
        is_active=True,
    )

    facebook_accounts = FacebookCredential.objects.filter(
        user=current_user,
        is_active=True,
    )

    x_accounts = XCredential.objects.filter(
        owner=current_user,
        is_active=True,
    )

    # ================================================
    # Background Music
    # ================================================

    background_music = _available_background_music_for_user(
        current_user
    )

    my_background_music = BackgroundMusic.objects.filter(
        user=current_user
    ).order_by("-created_at")

    # ================================================
    # Context
    # ================================================

    context = {
        "user": current_user,
        "user_settings": user_settings,
        "youtube_accounts": youtube_accounts,
        "instagram_accounts": instagram_accounts,
        "facebook_accounts": facebook_accounts,
        "x_accounts": x_accounts,
        "settings_saved": settings_saved,
        "profile_error": profile_error,
        "background_music": background_music,
        "my_background_music": my_background_music,
    }

    return render(
        request,
        "settings.html",
        context,
    )
