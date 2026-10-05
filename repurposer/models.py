from django.db import models
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
    token_json = models.TextField()

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
    token_json = models.TextField()

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
    token_json = models.TextField()

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
    token_json = models.TextField()

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