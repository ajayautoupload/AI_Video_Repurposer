from django.db import models
from django.utils import timezone
from .fields import EncryptedTextField

from django.contrib.auth.models import User





class Video(models.Model):



    # ================================================

    # Owner

    # ================================================



    user = models.ForeignKey(

        User,

        on_delete=models.CASCADE,

        blank=True,

        null=True,

        related_name='videos'

    )



    # Video source

    video_file = models.FileField(

        upload_to='videos/',

        max_length=500,

        blank=True,

        null=True

    )



    youtube_url = models.URLField(

        blank=True,

        null=True

    )

    # Public source details supplied by discovery workflows (for example,
    # YouTube Trending). Kept with the Video so background processing can
    # access the selected title/channel/context without a second lookup.
    source_metadata = models.JSONField(
        default=dict,
        blank=True,
    )



    # Selected publishing accounts

    selected_youtube_channel = models.ForeignKey(

        'YouTubeCredential',

        on_delete=models.SET_NULL,

        blank=True,

        null=True,

        related_name='videos'

    )



    selected_instagram_account = models.ForeignKey(

        'InstagramCredential',

        on_delete=models.SET_NULL,

        blank=True,

        null=True,

        related_name='videos'

    )



    selected_facebook_page = models.ForeignKey(

        'FacebookCredential',

        on_delete=models.SET_NULL,

        blank=True,

        null=True,

        related_name='videos'

    )



    selected_x_account = models.ForeignKey(

        'XCredential',

        on_delete=models.SET_NULL,

        blank=True,

        null=True,

        related_name='videos'

    )

    background_music = models.ForeignKey(
        "BackgroundMusic",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="videos_using_as_default_music",
    )



    # Processing settings

    PROCESSING_MODE_CHOICES = [

        ('viral', 'Viral Clip'),

        ('normal', 'Normal Cut Sequence'),

    ]



    processing_mode = models.CharField(

        max_length=20,

        choices=PROCESSING_MODE_CHOICES,

        default='viral'

    )



    OUTPUT_FORMAT_CHOICES = [

        ('vertical', 'Vertical 1080x1920'),

        ('horizontal', 'Horizontal 1920x1080'),

    ]



    output_format = models.CharField(

        max_length=20,

        choices=OUTPUT_FORMAT_CHOICES,

        default='vertical'

    )



    # ================================================

    # Workflow mode

    # ================================================



    WORKFLOW_MODE_CHOICES = [

        ('auto', 'Auto System'),

        ('manual', 'Manual System'),

    ]



    workflow_mode = models.CharField(

        max_length=20,

        choices=WORKFLOW_MODE_CHOICES,

        default='auto'

    )



    # Video information

    duration = models.FloatField(

        default=0

    )



    # ================================================

    # Processing progress

    # ================================================



    processing_progress = models.PositiveIntegerField(

        default=0

    )



    processing_stage = models.CharField(

        max_length=100,

        default='Waiting'

    )



    # Processing status

    STATUS_CHOICES = [

        ('uploaded', 'Uploaded'),

        ('processing', 'Processing'),

        ('completed', 'Completed'),

        ('failed', 'Failed'),

    ]



    status = models.CharField(

        max_length=20,

        choices=STATUS_CHOICES,

        default='uploaded'

    )



    # Timestamps

    created_at = models.DateTimeField(

        auto_now_add=True

    )



    updated_at = models.DateTimeField(

        auto_now=True

    )



    def __str__(self):

        if self.video_file:

            return self.video_file.name



        if self.youtube_url:

            return self.youtube_url



        return f"Video {self.id}"





class YouTubeCredential(models.Model):



    # ================================================

    # Owner

    # ================================================



    user = models.ForeignKey(

        User,

        on_delete=models.CASCADE,

        blank=True,

        null=True,

        related_name='youtube_credentials'

    )



    # YouTube channel ki pehchan

    channel_name = models.CharField(

        max_length=200

    )



    channel_id = models.CharField(

        max_length=200,

        unique=True,

        blank=True,

        null=True

    )



    # Google OAuth credentials

    token_json = EncryptedTextField()



    # Credential status

    is_active = models.BooleanField(

        default=True

    )



    # Timestamps

    created_at = models.DateTimeField(

        auto_now_add=True

    )



    updated_at = models.DateTimeField(

        auto_now=True

    )



    def __str__(self):

        return self.channel_name





class InstagramCredential(models.Model):



    # ================================================

    # Owner

    # ================================================



    user = models.ForeignKey(

        User,

        on_delete=models.CASCADE,

        blank=True,

        null=True,

        related_name='instagram_credentials'

    )



    # Instagram account ki pehchan

    account_name = models.CharField(

        max_length=200

    )



    account_id = models.CharField(

        max_length=200,

        unique=True,

        blank=True,

        null=True

    )



    # Meta OAuth credentials

    token_json = EncryptedTextField()



    # Credential status

    is_active = models.BooleanField(

        default=True

    )



    # Timestamps

    created_at = models.DateTimeField(

        auto_now_add=True

    )



    updated_at = models.DateTimeField(

        auto_now=True

    )



    def __str__(self):

        return self.account_name





class FacebookCredential(models.Model):



    # ================================================

    # Owner

    # ================================================



    user = models.ForeignKey(

        User,

        on_delete=models.CASCADE,

        blank=True,

        null=True,

        related_name='facebook_credentials'

    )



    # Facebook Page ki pehchan

    page_name = models.CharField(

        max_length=200

    )



    page_id = models.CharField(

        max_length=200,

        unique=True

    )



    # Facebook Page access token

    token_json = EncryptedTextField()



    # Credential status

    is_active = models.BooleanField(

        default=True

    )



    # Timestamps

    created_at = models.DateTimeField(

        auto_now_add=True

    )



    updated_at = models.DateTimeField(

        auto_now=True

    )



    def __str__(self):

        return self.page_name





class ScheduledPost(models.Model):



    # ================================================

    # Owner

    # ================================================



    user = models.ForeignKey(

        User,

        on_delete=models.CASCADE,

        blank=True,

        null=True,

        related_name='scheduled_posts'

    )



    # ========================================================

    # Source

    # ========================================================



    video = models.ForeignKey(

        Video,

        on_delete=models.CASCADE,

        related_name='scheduled_posts'

    )



    # Actual generated clip file path

    # Example:

    # processed/clip_1.mp4

    video_path = models.CharField(

        max_length=1000

    )



    # ========================================================

    # Publishing Platform

    # ========================================================



    PLATFORM_CHOICES = [

        ('youtube', 'YouTube'),

        ('instagram', 'Instagram'),

        ('facebook', 'Facebook'),

        ('x', 'X'),

    ]



    platform = models.CharField(

        max_length=20,

        choices=PLATFORM_CHOICES

    )



    # ========================================================

    # Destination Account

    # ========================================================



    youtube_channel = models.ForeignKey(

        YouTubeCredential,

        on_delete=models.SET_NULL,

        blank=True,

        null=True,

        related_name='scheduled_posts'

    )



    instagram_account = models.ForeignKey(

        InstagramCredential,

        on_delete=models.SET_NULL,

        blank=True,

        null=True,

        related_name='scheduled_posts'

    )



    facebook_page = models.ForeignKey(

        FacebookCredential,

        on_delete=models.SET_NULL,

        blank=True,

        null=True,

        related_name='scheduled_posts'

    )



    x_account = models.ForeignKey(

        'XCredential',

        on_delete=models.SET_NULL,

        blank=True,

        null=True,

        related_name='scheduled_posts'

    )



    # ========================================================

    # Post Information

    # ========================================================



    title = models.CharField(

        max_length=200,

        blank=True

    )



    description = models.TextField(

        blank=True

    )



    # ========================================================

    # Schedule

    # ========================================================



    scheduled_at = models.DateTimeField()



    # ========================================================

    # Scheduler Status

    # ========================================================



    STATUS_CHOICES = [

        ('scheduled', 'Scheduled'),

        ('processing', 'Processing'),

        ('published', 'Published'),

        ('failed', 'Failed'),

        ('cancelled', 'Cancelled'),

    ]



    status = models.CharField(

        max_length=20,

        choices=STATUS_CHOICES,

        default='scheduled'

    )



    # ========================================================

    # Platform Result

    # ========================================================



    external_post_id = models.CharField(

        max_length=300,

        blank=True

    )



    external_url = models.URLField(

        blank=True

    )



    error_message = models.TextField(

        blank=True

    )



    # ========================================================

    # Timestamps

    # ========================================================



    created_at = models.DateTimeField(

        auto_now_add=True

    )



    updated_at = models.DateTimeField(

        auto_now=True

    )



    def __str__(self):

        return (

            f"{self.platform} - "

            f"{self.title or 'Scheduled Post'} - "

            f"{self.scheduled_at}"

        )





class XCredential(models.Model):



    # Django user who owns this X account

    owner = models.ForeignKey(

        User,

        on_delete=models.CASCADE,

        blank=True,

        null=True,

        related_name='x_credentials'

    )



    # X account ki pehchan

    username = models.CharField(

        max_length=200

    )



    # X platform user ID

    user_id = models.CharField(

        max_length=200,

        unique=True,

        blank=True,

        null=True

    )



    # X OAuth 2.0 credentials

    token_json = EncryptedTextField()



    is_active = models.BooleanField(

        default=True

    )



    created_at = models.DateTimeField(

        auto_now_add=True

    )



    updated_at = models.DateTimeField(

        auto_now=True

    )



    def __str__(self):

        return self.username



# ============================================================

# User Settings

# ============================================================



class UserSettings(models.Model):



    # ================================================

    # Owner

    # ================================================



    user = models.OneToOneField(

        User,

        on_delete=models.CASCADE,

        related_name="settings",

    )

    profile_picture = models.FileField(
        upload_to="profile_pictures/",
        max_length=500,
        blank=True,
    )



    # ================================================

    # Video & Publishing Preferences

    # ================================================



    DEFAULT_OUTPUT_FORMAT_CHOICES = [

        ("vertical", "Vertical 1080x1920"),

        ("horizontal", "Horizontal 1920x1080"),

    ]



    DEFAULT_PROCESSING_MODE_CHOICES = [

        ("viral", "Viral Clip"),

        ("normal", "Normal Cut Sequence"),

    ]



    default_output_format = models.CharField(

        max_length=20,

        choices=DEFAULT_OUTPUT_FORMAT_CHOICES,

        default="vertical",

    )



    default_processing_mode = models.CharField(

        max_length=20,

        choices=DEFAULT_PROCESSING_MODE_CHOICES,

        default="viral",

    )



    auto_publish = models.BooleanField(

        default=False,

    )



    # ================================================

    # Schedule Preferences

    # ================================================



    timezone = models.CharField(

        max_length=100,

        default="Asia/Kolkata",

    )



    # ================================================

    # AI Preferences

    # ================================================



    ai_captions = models.BooleanField(

        default=True,

    )



    ai_titles = models.BooleanField(

        default=True,

    )



    ai_descriptions = models.BooleanField(

        default=True,

    )



    ai_hashtags = models.BooleanField(

        default=True,

    )



    # ================================================

    # Notification Preferences

    # ================================================



    processing_notifications = models.BooleanField(

        default=True,

    )



    publishing_notifications = models.BooleanField(

        default=True,

    )



    account_notifications = models.BooleanField(

        default=True,

    )



    # ================================================

    # Timestamps

    # ================================================



    created_at = models.DateTimeField(

        auto_now_add=True,

    )



    updated_at = models.DateTimeField(

        auto_now=True,

    )



    # ================================================

    # String

    # ================================================



    def __str__(self):

        return f"Settings - {self.user.username}"
# ============================================================
# Background Music Library
# ============================================================

class BackgroundMusic(models.Model):
    """
    Tracks may be privately retained from older uploads, shared by an
    uploader with all users, or global app defaults (user=None).

    Shared uploads remain owned by the uploader so they alone can delete them.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="background_music",
    )

    name = models.CharField(
        max_length=200,
    )

    audio_file = models.FileField(
        upload_to="background_music/",
        max_length=500,
    )

    is_default = models.BooleanField(
        default=False,
    )

    is_shared = models.BooleanField(
        default=False,
        db_index=True,
    )

    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return self.name


class Advertisement(models.Model):
    """Staff-managed media shown in the workspace dashboard ad slot."""

    PLACEMENT_CHOICES = [
        ("dashboard_top", "Dashboard top"),
        ("dashboard_bottom", "Dashboard bottom"),
    ]

    media_file = models.FileField(upload_to="advertisements/", max_length=500)
    destination_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    voice_enabled = models.BooleanField(default=True)
    duration_seconds = models.PositiveSmallIntegerField(default=8)
    placement = models.CharField(max_length=24, choices=PLACEMENT_CHOICES, default="dashboard_top")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.media_file.name


class AdminNotification(models.Model):
    TYPE_CHOICES = [
        ("announcement", "Announcement"),
        ("update", "Product update"),
        ("maintenance", "Maintenance"),
        ("warning", "Important notice"),
    ]
    PRIORITY_CHOICES = [("low", "Low"), ("normal", "Normal"), ("high", "High")]
    TARGET_CHOICES = [("all", "All users"), ("active", "Active users"), ("selected", "Selected users")]

    title = models.CharField(max_length=180)
    message = models.TextField(max_length=5000)
    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="announcement")
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="normal")
    target = models.CharField(max_length=12, choices=TARGET_CHOICES, default="all")
    users = models.ManyToManyField(User, through="NotificationRecipient", related_name="admin_notifications")
    image = models.FileField(upload_to="notifications/", blank=True, null=True)
    scheduled_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="created_admin_notifications")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class NotificationRecipient(models.Model):
    notification = models.ForeignKey(AdminNotification, on_delete=models.CASCADE, related_name="recipients")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notification_inbox")
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("notification", "user"), name="unique_notification_recipient")]
        indexes = [models.Index(fields=("user", "read_at", "created_at"))]


class PlatformMetricSnapshot(models.Model):
    """Daily verified metrics; growth is only reported when two snapshots exist."""
    PLATFORM_CHOICES = [("youtube", "YouTube"), ("facebook", "Facebook")]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="platform_metric_snapshots")
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    account_key = models.CharField(max_length=200)
    account_name = models.CharField(max_length=300, blank=True)
    snapshot_date = models.DateField(db_index=True)
    total_views = models.BigIntegerField(null=True, blank=True)
    followers = models.BigIntegerField(null=True, blank=True)
    captured_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "platform", "account_key", "snapshot_date"), name="unique_platform_metric_daily_snapshot")]
        ordering = ("-snapshot_date", "platform", "account_name")


# ============================================================
# Per-Clip Background Music Selection
# ============================================================

class ClipMusicSelection(models.Model):
    """
    Stores the background music selected for an individual
    generated clip.

    Each record belongs to one user and one source Video.
    video_path identifies the generated clip file, allowing
    every clip from the same video to have a different music
    selection.

    background_music=None means the user explicitly selected
    "No Music".
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="clip_music_selections",
    )

    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="music_selections",
    )

    video_path = models.CharField(
        max_length=1000,
    )

    background_music = models.ForeignKey(
        BackgroundMusic,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="clip_selections",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "video", "video_path"],
                name="unique_user_video_clip_music",
            ),
        ]

    def __str__(self):
        music_name = (
            self.background_music.name
            if self.background_music
            else "No Music"
        )

        return f"{self.video_path} - {music_name}"


class SubscriptionPlan(models.Model):
    """Admin-managed subscription catalog; prices and limits live in the DB."""
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="INR")
    duration_days = models.PositiveIntegerField(default=30)
    video_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Videos allowed for this subscription period; blank means unlimited.")
    account_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Connected accounts allowed per platform; blank means unlimited.")
    features = models.JSONField(default=list, blank=True)
    is_trial = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    priority_processing = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "price")

    def __str__(self):
        return self.name


class Subscription(models.Model):
    STATUS_CHOICES = [(value, value.replace("_", " ").title()) for value in ("trial", "active", "expired", "cancelled", "payment_pending")]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.SET_NULL, null=True, blank=True, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="trial", db_index=True)
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_grandfathered = models.BooleanField(default=False)
    renewal_enabled = models.BooleanField(default=False)
    videos_used = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-starts_at", "-pk")

    def __str__(self):
        return f"{self.user} · {self.plan or 'Legacy access'} · {self.status}"


class SubscriptionPayment(models.Model):
    STATUS_CHOICES = [(value, value.title()) for value in ("created", "pending", "captured", "failed", "refunded")]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="subscription_payments")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="payments")
    subscription = models.ForeignKey(Subscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    gateway = models.CharField(max_length=30, default="razorpay")
    order_id = models.CharField(max_length=160, unique=True)
    transaction_id = models.CharField(max_length=160, unique=True, null=True, blank=True)
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="created", db_index=True)
    payment_date = models.DateTimeField(null=True, blank=True)
    subscription_starts_at = models.DateTimeField(null=True, blank=True)
    subscription_expires_at = models.DateTimeField(null=True, blank=True)
    gateway_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user} · {self.order_id} · {self.status}"
