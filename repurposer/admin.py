from django.contrib import admin

from .models import (
    Video,
    YouTubeCredential,
    InstagramCredential,
    FacebookCredential,
    ScheduledPost,
    BackgroundMusic,
    Advertisement,
    SubscriptionPlan,
    Subscription,
    SubscriptionPayment,
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


@admin.register(BackgroundMusic)
class BackgroundMusicAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "is_shared", "is_default", "created_at")
    list_filter = ("is_shared", "is_default")
    search_fields = ("name", "user__username")


@admin.register(Advertisement)
class AdvertisementAdmin(admin.ModelAdmin):
    list_display = ("media_file", "destination_url", "is_active", "created_at")
    list_filter = ("is_active",)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "duration_days", "video_limit", "account_limit", "is_trial", "is_active")
    list_editable = ("price", "duration_days", "video_limit", "account_limit", "is_active")
    search_fields = ("name", "code")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "starts_at", "expires_at", "is_grandfathered")
    list_filter = ("status", "is_grandfathered", "plan")
    search_fields = ("user__username", "user__email")


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "amount", "currency", "status", "gateway", "created_at")
    list_filter = ("status", "gateway", "plan")
    search_fields = ("user__username", "order_id", "transaction_id")
    readonly_fields = ("user", "plan", "order_id", "transaction_id", "amount", "currency", "status", "gateway_data")
