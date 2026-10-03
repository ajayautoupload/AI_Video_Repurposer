from django.contrib import admin

from .models import (
    Video,
    YouTubeCredential,
    InstagramCredential,
    FacebookCredential,
    ScheduledPost,
)


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'video_file',
        'youtube_url',
        'processing_mode',
        'output_format',
        'status',
        'created_at',
    )


@admin.register(YouTubeCredential)
class YouTubeCredentialAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'channel_name',
        'channel_id',
        'is_active',
        'created_at',
        'updated_at',
    )


@admin.register(InstagramCredential)
class InstagramCredentialAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'account_name',
        'account_id',
        'is_active',
        'created_at',
        'updated_at',
    )


@admin.register(FacebookCredential)
class FacebookCredentialAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'page_name',
        'page_id',
        'is_active',
        'created_at',
        'updated_at',
    )


@admin.register(ScheduledPost)
class ScheduledPostAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'platform',
        'title',
        'scheduled_at',
        'status',
        'external_post_id',
        'created_at',
    )

    list_filter = (
        'platform',
        'status',
    )

    search_fields = (
        'title',
        'external_post_id',
    )