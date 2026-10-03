from django.urls import path

from . import views
from . import auth_views


urlpatterns = [
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
    # Main Dashboard
    # -----------------------------
    path(
        '',
        views.home,
        name='home',
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