from django.urls import path

from . import views
from . import auth_views
from . import admin_panel_views


urlpatterns = [
    path('admin/', admin_panel_views.dashboard, name='admin_panel_dashboard'),
    path('admin/content/<str:category>/', admin_panel_views.content_list, name='admin_panel_content'),
    path('admin/users/', admin_panel_views.users_list, name='admin_panel_users'),
    path('admin/users/<int:user_id>/', admin_panel_views.user_detail, name='admin_panel_user_detail'),
    path('admin/users/<int:user_id>/refresh-metrics/', admin_panel_views.refresh_user_metrics, name='admin_panel_refresh_metrics'),
    path('admin/users/<int:user_id>/<str:action>/', admin_panel_views.user_action, name='admin_panel_user_action'),
    path('admin/social-accounts/', admin_panel_views.social_accounts, name='admin_panel_social'),
    path('admin/growth/', admin_panel_views.growth_page, name='admin_panel_growth'),
    path('admin/analytics/', admin_panel_views.dashboard, name='admin_panel_analytics'),
    path('admin/progress-reports/', admin_panel_views.progress_reports, name='admin_panel_progress_reports'),
    path('admin/subscriptions/', admin_panel_views.subscriptions_management, name='admin_panel_subscriptions'),
    path('admin/subscriptions/<int:subscription_id>/action/', admin_panel_views.subscription_action, name='admin_panel_subscription_action'),
    path('admin/plans/', admin_panel_views.subscription_plans, name='admin_panel_plans'),
    path('admin/notifications/', admin_panel_views.notifications, name='admin_panel_notifications'),
    path('admin/notifications/create/', admin_panel_views.notification_form, name='admin_panel_notification_create'),
    path('admin/notifications/<int:notification_id>/edit/', admin_panel_views.notification_form, name='admin_panel_notification_edit'),
    path('admin/notifications/<int:notification_id>/<str:action>/', admin_panel_views.notification_action, name='admin_panel_notification_action'),
    path('admin/music/', admin_panel_views.music_management, name='admin_panel_music'),
    path('admin/music/<str:action>/', admin_panel_views.music_action, name='admin_panel_music_create'),
    path('admin/music/<int:music_id>/<str:action>/', admin_panel_views.music_action, name='admin_panel_music_action'),
    path('admin/advertisements/', admin_panel_views.advertisement_management, name='admin_panel_ads'),
    path('admin/advertisements/<str:action>/', admin_panel_views.advertisement_action, name='admin_panel_ad_create'),
    path('admin/advertisements/<int:ad_id>/<str:action>/', admin_panel_views.advertisement_action, name='admin_panel_ad_action'),
    path('notifications/', admin_panel_views.user_notifications, name='user_notifications'),
    path('notifications/<int:recipient_id>/read/', admin_panel_views.notification_read, name='notification_read'),
    path('subscription/', views.subscription_page, name='subscription_page'),
    path('subscription/plans/<int:plan_id>/order/', views.subscription_create_order, name='subscription_create_order'),
    path('subscription/verify/', views.subscription_verify_payment, name='subscription_verify_payment'),
    path('subscription/razorpay/webhook/', views.razorpay_webhook, name='razorpay_webhook'),
    # -----------------------------
    # Authentication
    # -----------------------------
    path(
        'register/',
        auth_views.register_view,
        name='register',
    ),

    path(
        'login/',
        auth_views.login_view,
        name='login',
    ),

    path(
        'logout/',
        auth_views.logout_view,
        name='logout',
    ),

    path(
        'verify-email/<uidb64>/<token>/',
        auth_views.verify_email,
        name='verify_email',
    ),

    # -----------------------------
    # Forgot Password
    # -----------------------------
    path(
        'password-reset/',
        auth_views.password_reset_request,
        name='password_reset_request',
    ),

    path(
        'password-reset/<uidb64>/<token>/',
        auth_views.password_reset_confirm,
        name='password_reset_confirm',
    ),

    # -----------------------------
    # Main Dashboard
    # -----------------------------
    path(
        '',
        views.home,
        name='home',
    ),

    path('videos/', views.videos_page, name='videos_page'),
    path('upload/', views.upload_video_page, name='upload_video_page'),
    path('schedule/', views.upcoming_schedule_page, name='upcoming_schedule_page'),
    path('clips/', views.generated_clips_page, name='generated_clips_page'),
    path('failed/', views.failed_items_page, name='failed_items_page'),
    path('analytics/', views.analytics_page, name='analytics_page'),
    path('workspace/', views.workspace_page, name='workspace_page'),
    path('progress-report/', views.progress_report_page, name='progress_report_page'),
    path('youtube-trending/', views.youtube_trending_page, name='youtube_trending_page'),
    path('accounts/', views.social_accounts_page, name='social_accounts_page'),
    path('videos/<int:video_id>/delete/', views.delete_video, name='delete_video'),
    path('advertisements/upload/', views.advertisement_upload, name='advertisement_upload'),

    path(
        'settings/',
        views.settings_view,
        name='settings',
    ),

    path(
        'settings/background-music/upload/',
        views.background_music_upload,
        name='background_music_upload',
    ),

    path(
        'settings/background-music/<int:music_id>/delete/',
        views.background_music_delete,
        name='background_music_delete',
    ),

    path(
        'clip-music/save/',
        views.save_clip_music_selection,
        name='save_clip_music_selection',
    ),

    path(
        'processing-status/<int:video_id>/',
        views.processing_status,
        name='processing_status',
    ),

    path(
        'manual/publish/',
        views.manual_publish,
        name='manual_publish',
    ),

    path(
        'manual/schedule/',
        views.manual_schedule,
        name='manual_schedule',
    ),

    # -----------------------------
    # YouTube
    # -----------------------------
    path(
        'youtube/login/',
        views.youtube_login,
        name='youtube_login',
    ),

    path(
        'youtube/callback/',
        views.youtube_callback,
        name='youtube_callback',
    ),

    # -----------------------------
    # Instagram
    # -----------------------------
    path(
        'instagram/login/',
        views.instagram_login,
        name='instagram_login',
    ),

    path(
        'instagram/callback/',
        views.instagram_callback,
        name='instagram_callback',
    ),

    # -----------------------------
    # Facebook
    # -----------------------------
    path(
        'facebook/login/',
        views.facebook_login,
        name='facebook_login',
    ),

    path(
        'facebook/callback/',
        views.facebook_callback,
        name='facebook_callback',
    ),

    # -----------------------------
    # X
    # -----------------------------
    path(
        'x/login/',
        views.x_login,
        name='x_login',
    ),

    path(
        'x/callback/',
        views.x_callback,
        name='x_callback',
    ),
]
