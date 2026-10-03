
import json

from apscheduler.schedulers.blocking import BlockingScheduler
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from repurposer.models import ScheduledPost
from repurposer.instagram_service import upload_reel_to_instagram


# ============================================================
# TARGET INSTAGRAM ACCOUNT AND CLIP FOLDER
# ============================================================

TARGET_INSTAGRAM_ACCOUNT_ID = 2

TARGET_VIDEO_PATH_PREFIX = "processed/normal/95/"


# ============================================================
# PUBLISH ONE INSTAGRAM REEL
# ============================================================

def publish_instagram_scheduled_post(post):
    """
    Publish a scheduled Reel only for the selected Instagram account
    and the intended clip folder.
    """

    if not post.instagram_account:
        raise ValueError(
            "Instagram account is not selected."
        )

    if post.instagram_account_id != TARGET_INSTAGRAM_ACCOUNT_ID:
        raise ValueError(
            "This post does not belong to the target Instagram account."
        )

    video_path = (post.video_path or "").replace("\\", "/").lstrip("/")

    if not video_path.startswith(TARGET_VIDEO_PATH_PREFIX):
        raise ValueError(
            "This video is outside the target clip folder."
        )

    token_data = json.loads(
        post.instagram_account.token_json
    )

    access_token = token_data.get("access_token")
    instagram_user_id = token_data.get("account_id")

    if not access_token:
        raise ValueError("Instagram access token is missing.")

    if not instagram_user_id:
        raise ValueError("Instagram account ID is missing.")

    public_video_url = (
        f"{settings.PUBLIC_BASE_URL.rstrip('/')}/"
        f"{settings.MEDIA_URL.strip('/')}/"
        f"{video_path}"
    )

    result = upload_reel_to_instagram(
        instagram_user_id=instagram_user_id,
        access_token=access_token,
        video_url=public_video_url,
        caption=post.description,
        use_facebook_graph=False,

    )

    return result


# ============================================================
# CHECK AND PUBLISH DUE POSTS
# ============================================================

def check_scheduled_posts():
    """
    Publish due Instagram posts only for account ID 2
    and the target clip folder.
    """

    now = timezone.now()

    scheduled_posts = (
        ScheduledPost.objects
        .filter(
            status="scheduled",
            platform="instagram",
            instagram_account_id=TARGET_INSTAGRAM_ACCOUNT_ID,
            video_path__startswith=TARGET_VIDEO_PATH_PREFIX,
            scheduled_at__lte=now,
        )
        .select_related("instagram_account")
        .order_by("scheduled_at")
    )

    if not scheduled_posts.exists():
        print(
            f"[Scheduler] {now} - "
            "No target Instagram posts are ready."
        )
        return

    for post in scheduled_posts:

        print(
            "[Scheduler] Target Instagram post ready:",
            f"ID={post.id}",
            f"Account ID={post.instagram_account_id}",
            f"Video={post.video_path}",
            f"Scheduled={post.scheduled_at}",
        )

        try:
            post.status = "processing"
            post.error_message = ""

            post.save(
                update_fields=[
                    "status",
                    "error_message",
                    "updated_at",
                ]
            )

            result = publish_instagram_scheduled_post(post)

            media_id = result.get("media_id")

            post.external_post_id = media_id or ""
            post.status = "published"
            post.error_message = ""

            post.save(
                update_fields=[
                    "external_post_id",
                    "status",
                    "error_message",
                    "updated_at",
                ]
            )

            print(
                "[Scheduler] Instagram published successfully:",
                f"Post ID={post.id}",
                f"Media ID={media_id}",
            )

        except Exception as exc:
            post.status = "failed"
            post.error_message = str(exc)

            post.save(
                update_fields=[
                    "status",
                    "error_message",
                    "updated_at",
                ]
            )

            print(
                "[Scheduler] Instagram publishing failed:",
                f"Post ID={post.id}",
                f"Error={exc}",
            )


# ============================================================
# DJANGO MANAGEMENT COMMAND
# ============================================================

class Command(BaseCommand):

    help = "Run the AI Video Repurposer Instagram scheduler."

    def handle(self, *args, **options):

        self.stdout.write(
            self.style.SUCCESS(
                "Instagram scheduler started."
            )
        )

        self.stdout.write(
            "Checking target account posts every 10 seconds..."
        )

        scheduler = BlockingScheduler()

        scheduler.add_job(
            check_scheduled_posts,
            "interval",
            seconds=10,
            id="scheduled_posts_checker",
            replace_existing=True,
        )

        # Run one check immediately.
        check_scheduled_posts()

        try:
            scheduler.start()

        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING(
                    "Scheduler stopped."
                )
            )

            scheduler.shutdown(wait=False)