
import json
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings

from repurposer.models import ScheduledPost
from repurposer.instagram_service import upload_reel_to_instagram
from repurposer.media_access import signed_public_media_url
from repurposer.facebook_service import upload_video_to_facebook
from repurposer.x_service import publish_video_to_x


# ============================================================
# PUBLISH ONE INSTAGRAM REEL
# ============================================================

def publish_instagram_scheduled_post(post):
    """
    Publish a scheduled Reel for its selected Instagram account.
    """

    if not post.instagram_account:
        raise ValueError(
            "Instagram account is not selected."
        )

    video_path = (post.video_path or "").replace("\\", "/").lstrip("/")
    if not video_path.startswith("processed/") or ".." in video_path.split("/"):
        raise ValueError("Scheduled video path is invalid.")

    token_data = json.loads(
        post.instagram_account.token_json
    )

    access_token = token_data.get("access_token")
    instagram_user_id = token_data.get("account_id")

    if not access_token:
        raise ValueError("Instagram access token is missing.")

    if not instagram_user_id:
        raise ValueError("Instagram account ID is missing.")

    public_video_url = signed_public_media_url(video_path)

    result = upload_reel_to_instagram(
        instagram_user_id=instagram_user_id,
        access_token=access_token,
        video_url=public_video_url,
        caption=post.description,
        use_facebook_graph=False,

    )

    return result


def publish_due_post(post):
    """Publish a due Instagram, Facebook, or X post locally."""
    relative_path = (post.video_path or "").replace("\\", "/").lstrip("/")
    if not relative_path.startswith("processed/") or ".." in relative_path.split("/"):
        raise ValueError("Scheduled video path is invalid.")
    video_file = (Path(settings.MEDIA_ROOT) / relative_path).resolve()
    media_root = Path(settings.MEDIA_ROOT).resolve()
    if media_root not in video_file.parents or not video_file.is_file():
        raise FileNotFoundError("Scheduled video file is missing or invalid.")

    if post.platform == "instagram":
        return publish_instagram_scheduled_post(post)
    if post.platform == "facebook":
        if not post.facebook_page:
            raise ValueError("Facebook Page is not selected.")
        token_data = json.loads(post.facebook_page.token_json)
        page_token = token_data.get("access_token")
        page_id = token_data.get("page_id") or post.facebook_page.page_id
        if not page_token:
            raise ValueError("Facebook Page access token is missing.")
        result = upload_video_to_facebook(
            page_id=page_id,
            page_access_token=page_token,
            video_path=str(video_file),
            title=post.title,
            description=post.description,
        )
        return {"media_id": result.get("video_id"), "url": ""}
    if post.platform == "x":
        if not post.x_account:
            raise ValueError("X account is not selected.")
        result = publish_video_to_x(
            post.x_account,
            str(video_file),
            text=post.description,
        )
        return {"media_id": result.get("media_id"), "post_id": result.get("post_id")}
    raise ValueError(f"Unsupported scheduled platform: {post.platform}")


# ============================================================
# CHECK AND PUBLISH DUE POSTS
# ============================================================

def check_scheduled_posts():
    """
    Publish all locally scheduled posts that are due. YouTube is
    scheduled natively during upload and is intentionally excluded.
    """

    now = timezone.now()

    scheduled_posts = (
        ScheduledPost.objects
        .filter(
            status="scheduled",
            platform__in=("instagram", "facebook", "x"),
            scheduled_at__lte=now,
        )
        .select_related("instagram_account", "facebook_page", "x_account")
        .order_by("scheduled_at")
    )

    if not scheduled_posts.exists():
        print(
            f"[Scheduler] {now} - "
            "No locally scheduled posts are ready."
        )
        return

    for post in scheduled_posts:

        print(
            "[Scheduler] Scheduled post ready:",
            f"Platform={post.platform}",
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

            result = publish_due_post(post)

            media_id = result.get("media_id")

            post.external_post_id = media_id or ""
            if result.get("post_id"):
                post.external_post_id = str(result["post_id"])
            post.external_url = result.get("url") or post.external_url
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
                f"[Scheduler] {post.platform} published successfully:",
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
                f"[Scheduler] {post.platform} publishing failed:",
                f"Post ID={post.id}",
                f"Error={exc}",
            )


# ============================================================
# DJANGO MANAGEMENT COMMAND
# ============================================================

class Command(BaseCommand):

    help = "Run the AI Video Repurposer local publishing scheduler."

    def handle(self, *args, **options):

        self.stdout.write(
            self.style.SUCCESS(
                "Local publishing scheduler started."
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
