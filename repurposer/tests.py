import tempfile
import json
import hashlib
import hmac
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from urllib.parse import urlsplit, parse_qs

from .models import (
    AdminNotification,
    BackgroundMusic,
    ClipMusicSelection,
    InstagramCredential,
    ScheduledPost,
    Video,
    YouTubeCredential,
    NotificationRecipient,
    PlatformMetricSnapshot,
    Subscription,
    SubscriptionPayment,
    SubscriptionPlan,
)
from .views import _available_background_music_for_user, get_generated_clips
from .media_access import signed_public_media_url
from .subscriptions import account_entitlement


class ClipMusicSelectionTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent
        )
        self.addCleanup(self.media_directory.cleanup)
        self.settings_context = override_settings(
            MEDIA_ROOT=Path(self.media_directory.name),
            MEDIA_URL="media/",
        )
        self.settings_context.enable()
        self.addCleanup(self.settings_context.disable)

        self.user = User.objects.create_user(
            username="music-owner",
            password="test-password",
        )
        self.other_user = User.objects.create_user(
            username="other-owner",
            password="test-password",
        )
        self.video = Video.objects.create(
            user=self.user,
            processing_mode="viral",
            output_format="vertical",
            status="completed",
        )
        self.output_directory = (
            Path(self.media_directory.name)
            / "processed"
            / "viral"
            / str(self.video.id)
        )
        vertical_directory = (
            Path(self.media_directory.name)
            / "processed"
            / "vertical"
            / str(self.video.id)
        )
        self.output_directory.mkdir(parents=True)
        vertical_directory.mkdir(parents=True)
        for number in (1, 2, 3):
            (self.output_directory / f"clip_{number}.mp4").write_bytes(b"source")
            (vertical_directory / f"vertical_clip_{number}.mp4").write_bytes(b"vertical")

        self.music_a = self._music(self.user, "Music A")
        self.music_b = self._music(self.user, "Music B")
        self.other_music = self._music(self.other_user, "Private music")
        self.client.force_login(self.user)

    def _music(self, owner, name):
        return BackgroundMusic.objects.create(
            user=owner,
            name=name,
            audio_file=SimpleUploadedFile(
                f"{name.lower().replace(' ', '-')}.mp3",
                b"not real audio; render is mocked",
                content_type="audio/mpeg",
            ),
        )

    def _clip(self, number):
        return next(
            clip for clip in get_generated_clips(self.video)
            if clip["number"] == number
        )

    def _save(self, clip, music_id):
        return self.client.post(
            "/clip-music/save/",
            {
                "video_id": self.video.id,
                "video_path": clip["source_file_path"],
                "background_music_id": music_id or "",
            },
        )

    @patch("repurposer.views.create_vertical_short")
    def test_saves_distinct_music_and_no_music_per_clip(self, render):
        def fake_render(input_path, output_path, bgm_path=None):
            Path(output_path).write_bytes(
                b"rendered-with-music" if bgm_path else b"rendered-no-music"
            )

        render.side_effect = fake_render
        self.assertEqual(self._save(self._clip(1), self.music_a.id).status_code, 200)
        self.assertEqual(self._save(self._clip(2), self.music_b.id).status_code, 200)
        self.assertEqual(self._save(self._clip(3), "").status_code, 200)

        refreshed = {clip["number"]: clip for clip in get_generated_clips(self.video)}
        self.assertEqual(refreshed[1]["selected_music_id"], self.music_a.id)
        self.assertEqual(refreshed[2]["selected_music_id"], self.music_b.id)
        self.assertIsNone(refreshed[3]["selected_music_id"])
        self.assertEqual(
            Path(refreshed[3]["file_path"]).read_bytes(),
            b"rendered-no-music",
        )

    def test_rejects_another_users_music(self):
        response = self._save(self._clip(1), self.other_music.id)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ClipMusicSelection.objects.filter(video=self.video).exists()
        )

    @patch("repurposer.views.create_vertical_short")
    def test_shared_music_is_available_to_other_users(self, render):
        self.music_a.is_shared = True
        self.music_a.save(update_fields=["is_shared"])
        other_video = Video.objects.create(
            user=self.other_user,
            processing_mode="viral",
            output_format="vertical",
            status="completed",
        )
        source_dir = Path(self.media_directory.name) / "processed" / "viral" / str(other_video.id)
        vertical_dir = Path(self.media_directory.name) / "processed" / "vertical" / str(other_video.id)
        source_dir.mkdir(parents=True)
        vertical_dir.mkdir(parents=True)
        (source_dir / "clip_1.mp4").write_bytes(b"other-source")
        (vertical_dir / "vertical_clip_1.mp4").write_bytes(b"other-vertical")

        self.assertIn(self.music_a, _available_background_music_for_user(self.other_user))
        self.client.force_login(self.other_user)
        self.assertEqual(
            self.client.get(f"/media/{self.music_a.audio_file.name}").status_code,
            200,
        )
        clip = get_generated_clips(other_video)[0]
        render.side_effect = lambda input_path, output_path, bgm_path=None: Path(output_path).write_bytes(b"shared")
        response = self.client.post(
            "/clip-music/save/",
            {
                "video_id": other_video.id,
                "video_path": clip["source_file_path"],
                "background_music_id": self.music_a.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["music_id"], self.music_a.id)

    @patch("repurposer.views.apply_background_music")
    def test_global_music_is_allowed_for_horizontal_output(self, render):
        def fake_render(input_path, output_path, bgm_path=None):
            Path(output_path).write_bytes(b"horizontal-with-music")

        render.side_effect = fake_render
        self.video.output_format = "horizontal"
        self.video.save(update_fields=["output_format"])
        global_music = BackgroundMusic.objects.create(
            user=None,
            name="Global default",
            is_default=True,
            audio_file=SimpleUploadedFile(
                "global-default.mp3",
                b"not real audio; render is mocked",
                content_type="audio/mpeg",
            ),
        )

        response = self._save(self._clip(1), global_music.id)

        self.assertEqual(response.status_code, 200)
        render.assert_called_once()
        self.assertEqual(
            ClipMusicSelection.objects.get(video=self.video).background_music,
            global_music,
        )

    def test_saved_clip_music_is_loaded_for_dashboard(self):
        ClipMusicSelection.objects.create(
            user=self.user,
            video=self.video,
            video_path=self._clip(2)["selection_path"],
            background_music=self.music_b,
        )
        rendered_directory = (
            Path(self.media_directory.name)
            / "processed"
            / "music"
            / str(self.video.id)
        )
        rendered_directory.mkdir(parents=True)
        (rendered_directory / "clip_2.mp4").write_bytes(b"rendered")
        clip = self._clip(2)
        self.assertEqual(clip["selected_music_id"], self.music_b.id)
        self.assertEqual(Path(clip["file_path"]).parent.parent.name, "music")

    def test_horizontal_selection_uses_horizontal_source_even_if_vertical_exists(self):
        self.video.output_format = "horizontal"
        self.video.save(update_fields=["output_format"])

        clip = self._clip(1)

        self.assertIn("processed/viral/", clip["path"])
        self.assertNotIn("processed/vertical/", clip["path"])

    def test_media_is_owner_only_or_uses_a_signed_external_url(self):
        relative_path = f"processed/viral/{self.video.id}/clip_1.mp4"
        direct_url = f"/media/{relative_path}"

        self.client.logout()
        self.assertEqual(self.client.get(direct_url).status_code, 403)

        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(direct_url).status_code, 403)

        self.client.force_login(self.user)
        response = self.client.get(direct_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(b"".join(response.streaming_content), b"source")

        signed = signed_public_media_url(relative_path)
        parsed = urlsplit(signed)
        token = parse_qs(parsed.query)["token"][0]
        self.client.logout()
        external_response = self.client.get(f"{direct_url}?token={token}")
        self.assertEqual(external_response.status_code, 200)
        self.assertEqual(
            b"".join(external_response.streaming_content),
            b"source",
        )


class DashboardGenerateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="generate-user",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_login_redirects_to_video_upload_instead_of_next_destination(self):
        self.client.logout()
        response = self.client.post(
            "/login/?next=/analytics/",
            {"username": "generate-user", "password": "test-password"},
        )
        self.assertRedirects(response, "/upload/", fetch_redirect_response=False)

    def test_trial_allows_ten_videos_then_blocks_generate_before_saving(self):
        trial = Subscription.objects.get(user=self.user)
        self.assertEqual(trial.status, "trial")
        self.assertEqual(trial.plan.code, "free_trial")
        for _ in range(10):
            Video.objects.create(user=self.user, status="completed")

        # Removing an uploaded item must not restore a consumed trial credit.
        first_video = Video.objects.filter(user=self.user).first()
        self.client.post(f"/videos/{first_video.id}/delete/")
        self.assertEqual(Video.objects.filter(user=self.user).count(), 9)

        response = self.client.post("/", {
            "workflow_mode": "manual", "processing_mode": "viral",
            "output_format": "vertical", "youtube_url": "https://www.youtube.com/watch?v=blocked12345",
        })

        self.assertRedirects(response, "/subscription/", fetch_redirect_response=False)
        self.assertEqual(Video.objects.filter(user=self.user).count(), 9)
        self.assertContains(self.client.get("/subscription/"), "Choose your plan")

    def test_subscription_page_shows_usage_plan_options_and_account_totals(self):
        response = self.client.get("/subscription/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Free Trial")
        self.assertContains(response, "Monthly")
        self.assertContains(response, "6 Months")
        self.assertContains(response, "Yearly")
        self.assertContains(response, "Videos used")
        self.assertContains(response, "Connected accounts")

    def test_subscription_limits_apply_per_platform_and_allow_reconnect(self):
        self.user_subscription = Subscription.objects.get(user=self.user)
        for index in range(2):
            YouTubeCredential.objects.create(
                user=self.user, channel_name=f"Channel {index}", channel_id=f"yt-{self.user.id}-{index}",
                token_json="{}", is_active=True,
            )
        allowed, message = account_entitlement(self.user, "youtube")
        self.assertFalse(allowed)
        self.assertIn("limit of 2 YouTube", message)
        self.assertEqual(account_entitlement(self.user, "youtube", existing=True), (True, None))

    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="")
    def test_paid_order_is_not_created_without_gateway_credentials(self):
        plan = SubscriptionPlan.objects.get(code="monthly")
        response = self.client.post(f"/subscription/plans/{plan.id}/order/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(SubscriptionPayment.objects.filter(user=self.user).count(), 0)

    @override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="test-secret")
    @patch("repurposer.subscriptions.requests.get")
    def test_only_verified_captured_payment_activates_subscription(self, gateway_get):
        plan = SubscriptionPlan.objects.get(code="monthly")
        payment = SubscriptionPayment.objects.create(user=self.user, plan=plan, order_id="order_test", amount=plan.price)
        payment_id = "pay_test"
        signature = hmac.new(b"test-secret", b"order_test|pay_test", hashlib.sha256).hexdigest()
        gateway_get.return_value.json.return_value = {"status": "captured", "order_id": "order_test", "amount": 49900, "currency": "INR"}
        gateway_get.return_value.raise_for_status.return_value = None

        response = self.client.post("/subscription/verify/", data=json.dumps({
            "razorpay_order_id": "order_test", "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        }), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "captured")
        paid_sub = Subscription.objects.filter(user=self.user, plan=plan).latest("starts_at")
        self.assertEqual(paid_sub.status, "active")
        self.assertGreater(paid_sub.expires_at, paid_sub.starts_at)
        duplicate = self.client.post("/subscription/verify/", data=json.dumps({
            "razorpay_order_id": "order_test", "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        }), content_type="application/json")
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(Subscription.objects.filter(user=self.user, plan=plan).count(), 1)

    @override_settings(CELERY_BROKER_URL="", CELERY_RESULT_BACKEND="")
    @patch("threading.Thread.start")
    def test_generate_starts_without_a_celery_broker(self, start_thread):
        response = self.client.post(
            "/",
            {
                "workflow_mode": "manual",
                "processing_mode": "viral",
                "output_format": "vertical",
                "youtube_url": "https://www.youtube.com/watch?v=test1234567",
            },
        )

        self.assertEqual(response.status_code, 302)
        video = Video.objects.get(user=self.user)
        self.assertEqual(video.status, "processing")
        start_thread.assert_called_once()

    def test_workspace_sidebar_pages_render_independently(self):
        for path in (
            "/",
            "/videos/",
            "/upload/",
            "/schedule/",
            "/clips/",
            "/failed/",
            "/analytics/",
            "/workspace/",
            "/progress-report/",
            "/youtube-trending/",
            "/accounts/",
            "/settings/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        settings_response = self.client.get("/settings/")
        self.assertNotContains(settings_response, "Back to Dashboard")
        self.assertNotContains(settings_response, "Go to Dashboard")
        self.assertContains(settings_response, "Progress Report")
        self.assertContains(settings_response, "YouTube Trending")
        self.assertContains(settings_response, 'id="settingsMenuButton"')
        self.assertContains(settings_response, 'id="settingsBackButton"')

        mobile_nav_response = self.client.get("/videos/")
        self.assertContains(mobile_nav_response, 'id="mobileMenuButton"')
        self.assertContains(mobile_nav_response, 'id="sidebarBackdrop"')

    def test_video_delete_removes_only_requested_owned_video(self):
        video = Video.objects.create(user=self.user, status="completed")
        response = self.client.post(f"/videos/{video.id}/delete/")
        self.assertRedirects(response, "/videos/", fetch_redirect_response=False)
        self.assertFalse(Video.objects.filter(pk=video.pk).exists())

        other_user = User.objects.create_user(username="other-delete-user")
        other_video = Video.objects.create(user=other_user, status="completed")
        denied = self.client.post(f"/videos/{other_video.id}/delete/")
        self.assertEqual(denied.status_code, 404)
        self.assertTrue(Video.objects.filter(pk=other_video.pk).exists())

        processing_video = Video.objects.create(user=self.user, status="processing")
        blocked = self.client.post(f"/videos/{processing_video.id}/delete/")
        self.assertRedirects(blocked, "/videos/", fetch_redirect_response=False)
        self.assertTrue(Video.objects.filter(pk=processing_video.pk).exists())

    @patch("repurposer.views.get_facebook_dashboard_analytics")
    @patch("repurposer.views.get_youtube_dashboard_analytics")
    def test_progress_report_uses_real_analytics_without_duplicate_progress(self, youtube_analytics, facebook_analytics):
        youtube_analytics.return_value = {"available": True, "accounts": [{"available": True, "total_channel_views": 450, "subscribers": 27}]}
        facebook_analytics.return_value = {"available": True, "accounts": [{"available": True, "followers": 13}]}
        response = self.client.get("/progress-report/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "450")
        self.assertContains(response, "40")
        self.assertEqual(response.content.decode().count("Platform progress</h2>"), 1)

    def test_social_accounts_show_live_connected_account_count_and_platform_icons(self):
        response = self.client.get("/accounts/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connected Accounts: 0")
        for platform in ("youtube", "instagram", "facebook", "x-icon"):
            self.assertContains(response, f'class="platform-icon {platform}"')

    @patch("repurposer.views.get_youtube_trend_context")
    def test_trending_videos_render_ranked_list_layout(self, get_trends):
        get_trends.return_value = {
            "available": True, "status": "live", "count": 1,
            "videos": [{"title": "Ranked video", "channel_title": "Channel", "url": "https://youtube.com/watch?v=ranked", "thumbnail": "https://example.test/thumb.jpg", "views": 800, "likes": 12}],
        }
        response = self.client.get("/youtube-trending/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "trend-list")
        self.assertContains(response, "trend-rank")
        self.assertContains(response, "Ranked video")
        self.assertContains(response, "Process Video")
        self.assertContains(response, 'name="source_channel_title" value="Channel"')

    def test_trending_selection_prefills_existing_upload_form_and_metadata(self):
        response = self.client.get("/upload/", {
            "youtube_url": "https://www.youtube.com/watch?v=trend9876543",
            "source_title": "Selected trending title",
            "source_channel_title": "Selected channel",
            "source_channel_id": "UC123",
            "source_thumbnail": "https://example.test/thumb.jpg",
            "source_published_at": "2026-10-20T10:00:00Z",
            "source_description": "Public description",
            "source_category_id": "24",
            "source_tags": json.dumps(["news", "shorts"]),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"]["youtube_url"].value(),
            "https://www.youtube.com/watch?v=trend9876543",
        )
        self.assertEqual(response.context["trending_metadata"]["tags"], ["news", "shorts"])
        self.assertContains(response, "Selected trending title")
        self.assertContains(response, 'name="source_metadata"')

    @override_settings(CELERY_BROKER_URL="", CELERY_RESULT_BACKEND="")
    @patch("threading.Thread.start")
    def test_generate_keeps_trending_metadata_on_the_video(self, start_thread):
        metadata = {
            "title": "Trending source",
            "channel_title": "News Channel",
            "channel_id": "UC987",
            "thumbnail": "https://example.test/trending.jpg",
            "published_at": "2026-10-20T10:00:00Z",
            "description": "Selected public source",
            "category_id": "25",
            "tags": ["breaking", "video"],
        }
        response = self.client.post("/", {
            "workflow_mode": "manual",
            "processing_mode": "viral",
            "output_format": "vertical",
            "youtube_url": "https://www.youtube.com/watch?v=trend9876543",
            "source_metadata": json.dumps(metadata),
        })
        self.assertEqual(response.status_code, 302)
        video = Video.objects.get(user=self.user)
        self.assertEqual(video.youtube_url, "https://www.youtube.com/watch?v=trend9876543")
        self.assertEqual(video.source_metadata, metadata)
        start_thread.assert_called_once()

    @patch("repurposer.views.get_youtube_trend_context")
    def test_youtube_trending_page_uses_live_trend_service_and_region(self, get_trends):
        get_trends.return_value = {
            "available": True,
            "status": "live",
            "count": 1,
            "videos": [{
                "video_id": "trending123",
                "title": "Trending test video",
                "channel_title": "Test channel",
                "url": "https://www.youtube.com/watch?v=trending123",
                "thumbnail": "https://example.test/thumbnail.jpg",
                "views": 1234,
                "likes": 50,
            }],
        }
        response = self.client.get("/youtube-trending/?region=US")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Trending test video")
        get_trends.assert_called_once_with(
            api_key=settings.YOUTUBE_API_KEY,
            region_code="US",
            max_results=25,
        )

    def test_upload_page_has_optional_music_selection(self):
        response = self.client.get("/upload/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Background music (optional)")
        self.assertContains(response, "Choose a shared track")
        self.assertContains(response, 'id="videoUploadForm"')
        self.assertContains(response, 'id="localVideoPreview"')
        self.assertContains(response, "Generate clips")
        content = response.content.decode()
        self.assertLess(content.index(">Video Upload</span>"), content.index(">Workspace</span>"))

    @override_settings(CELERY_BROKER_URL="", CELERY_RESULT_BACKEND="")
    def test_dashboard_keeps_music_library_off_home(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Background music library")


class UploadMusicPipelineTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.media_directory.cleanup)
        self.settings_context = override_settings(MEDIA_ROOT=Path(self.media_directory.name), MEDIA_URL="/media/")
        self.settings_context.enable()
        self.addCleanup(self.settings_context.disable)
        self.user = User.objects.create_user(username="video-music-user")
        self.music = BackgroundMusic.objects.create(
            user=self.user,
            name="Upload selection music",
            audio_file=SimpleUploadedFile("upload-selection.mp3", b"mock audio"),
            is_shared=True,
        )
        self.video = Video.objects.create(
            user=self.user,
            processing_mode="viral",
            output_format="vertical",
            workflow_mode="manual",
            background_music=self.music,
            status="processing",
        )

    @patch("repurposer.views.create_vertical_short")
    @patch("repurposer.views.apply_vertical_compositor")
    @patch("repurposer.views.process_video")
    def test_upload_time_music_is_applied_after_clips_are_generated(
        self, process, compositor, render
    ):
        source_dir = Path(self.media_directory.name) / "processed" / "viral" / str(self.video.id)
        vertical_dir = Path(self.media_directory.name) / "processed" / "vertical" / str(self.video.id)
        source_dir.mkdir(parents=True)
        vertical_dir.mkdir(parents=True)
        source_path = source_dir / "clip_1.mp4"
        source_path.write_bytes(b"source")
        (vertical_dir / "vertical_clip_1.mp4").write_bytes(b"vertical")
        process.return_value = [{"number": 1, "file_path": str(source_path), "path": ""}]
        compositor.side_effect = lambda _video, clips: clips
        render.side_effect = lambda _source, output, bgm_path=None: Path(output).write_bytes(b"music-render")

        from .views import background_process_video
        background_process_video(self.video.id)

        self.video.refresh_from_db()
        self.assertEqual(self.video.status, "completed")
        selection = ClipMusicSelection.objects.get(video=self.video)
        self.assertEqual(selection.background_music, self.music)
        self.assertEqual(
            (Path(self.media_directory.name) / "processed" / "music" / str(self.video.id) / "clip_1.mp4").read_bytes(),
            b"music-render",
        )


class SchedulerTests(TestCase):
    @patch("repurposer.management.commands.run_scheduler.publish_due_post")
    def test_due_instagram_post_is_not_limited_to_one_account_or_folder(self, publish):
        user = User.objects.create_user(username="scheduled-user")
        video = Video.objects.create(user=user, status="completed")
        account = InstagramCredential.objects.create(
            user=user,
            account_name="My account",
            token_json='{"access_token":"test","account_id":"123"}',
        )
        post = ScheduledPost.objects.create(
            user=user,
            video=video,
            video_path=f"processed/vertical/{video.id}/vertical_clip_1.mp4",
            platform="instagram",
            instagram_account=account,
            title="Test",
            description="Test scheduled clip",
            scheduled_at=timezone.now() - timedelta(minutes=2),
        )
        publish.return_value = {"media_id": "published-id"}

        from .management.commands.run_scheduler import check_scheduled_posts
        check_scheduled_posts()

        post.refresh_from_db()
        self.assertEqual(post.status, "published")
        self.assertEqual(post.external_post_id, "published-id")
        publish.assert_called_once()


class CredentialEncryptionTests(TestCase):
    def test_platform_token_is_encrypted_at_rest_and_transparent_to_callers(self):
        user = User.objects.create_user(username="token-user")
        token = '{"access_token":"private-test-token"}'
        credential = YouTubeCredential.objects.create(
            user=user,
            channel_name="Test channel",
            token_json=token,
        )

        credential.refresh_from_db()
        self.assertEqual(credential.token_json, token)

        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT token_json FROM repurposer_youtubecredential WHERE id = %s",
                [credential.id],
            )
            stored_value = cursor.fetchone()[0]
        self.assertTrue(stored_value.startswith("enc:v1:"))
        self.assertNotIn("private-test-token", stored_value)


class AdminPanelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="panel-admin", email="admin@example.test", password="test-password"
        )
        self.user = User.objects.create_user(username="panel-user", password="test-password")

    def test_admin_routes_are_staff_only(self):
        self.client.force_login(self.user)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_admin_pages_render_against_database(self):
        self.client.force_login(self.admin)
        for route in (
            "/admin/",
            "/admin/auth/user/",
            "/admin/repurposer/video/",
            "/admin/content/videos/",
            "/admin/content/processing/",
            "/admin/content/scheduled/",
            "/admin/content/published/",
            "/admin/content/failed/",
            "/admin/users/",
            f"/admin/users/{self.user.id}/",
            "/admin/social-accounts/",
            "/admin/growth/",
            "/admin/notifications/",
            "/admin/music/",
            "/admin/advertisements/",
            "/admin/subscriptions/",
            "/admin/plans/",
        ):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
        self.assertContains(self.client.get("/admin/"), "Active paid subscribers")

    def test_old_separate_admin_panel_path_is_removed(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/admin-panel/").status_code, 404)

    def test_notification_delivery_read_status_and_user_inbox(self):
        self.client.force_login(self.admin)
        response = self.client.post("/admin/notifications/create/", {
            "title": "Service update",
            "message": "Processing is available.",
            "notification_type": "update",
            "priority": "normal",
            "target": "selected",
            "users": [self.user.id],
            "is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        recipient = NotificationRecipient.objects.get(user=self.user)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/notifications/").status_code, 200)
        response = self.client.post(f"/notifications/{recipient.id}/read/")
        self.assertEqual(response.status_code, 302)
        recipient.refresh_from_db()
        self.assertIsNotNone(recipient.read_at)

    def test_growth_is_based_on_two_real_snapshots(self):
        from .admin_panel_views import _growth_rows

        today = timezone.localdate()
        PlatformMetricSnapshot.objects.create(
            user=self.user, platform="youtube", account_key="channel-1",
            snapshot_date=today - timedelta(days=1), followers=100, total_views=1000,
        )
        PlatformMetricSnapshot.objects.create(
            user=self.user, platform="youtube", account_key="channel-1",
            snapshot_date=today, followers=125, total_views=1300,
        )
        row = next(item for item in _growth_rows() if item["user"].id == self.user.id)
        self.assertEqual(row["growth"], 25)
        self.assertEqual(row["growth_percent"], 25.0)

    def test_admin_can_create_shared_music_and_disable_it_for_users(self):
        import tempfile
        from pathlib import Path
        media = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(media.cleanup)
        with override_settings(MEDIA_ROOT=Path(media.name)):
            self.client.force_login(self.admin)
            response = self.client.post("/admin/music/create/", {
                "new-name": "Shared track",
                "new-audio_file": SimpleUploadedFile("track.mp3", b"audio", content_type="audio/mpeg"),
                "new-is_active": "on",
                "new-is_default": "on",
            })
            self.assertEqual(response.status_code, 302)
            music = BackgroundMusic.objects.get(name="Shared track")
            self.assertTrue(music.is_shared)
            self.assertTrue(music.is_default)
            self.assertIn(music, _available_background_music_for_user(self.user))
            self.client.post(f"/admin/music/{music.id}/toggle/")
            music.refresh_from_db()
            self.assertFalse(music.is_active)
            self.assertNotIn(music, _available_background_music_for_user(self.user))

    def test_admin_can_configure_and_disable_advertisement(self):
        import tempfile
        from pathlib import Path
        from .models import Advertisement

        media = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(media.cleanup)
        with override_settings(MEDIA_ROOT=Path(media.name)):
            self.client.force_login(self.admin)
            response = self.client.post("/admin/advertisements/create/", {
                "new-media_file": SimpleUploadedFile("promo.mp4", b"video", content_type="video/mp4"),
                "new-destination_url": "https://example.com/promo",
                "new-is_active": "on",
                "new-duration_seconds": "11",
                "new-placement": "dashboard_bottom",
                "new-voice_enabled": "on",
            })
            self.assertEqual(response.status_code, 302)
            ad = Advertisement.objects.get()
            self.assertEqual(ad.placement, "dashboard_bottom")
            self.assertEqual(ad.duration_seconds, 11)
            self.assertTrue(ad.voice_enabled)
            self.client.post(f"/admin/advertisements/{ad.id}/toggle/")
            ad.refresh_from_db()
            self.assertFalse(ad.is_active)
