from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .admin_panel_forms import AdminAdvertisementForm, AdminMusicForm, AdminNotificationForm, SubscriptionPlanForm
from .facebook_analytics_service import get_facebook_dashboard_analytics
from .models import (
    AdminNotification,
    Advertisement,
    BackgroundMusic,
    FacebookCredential,
    InstagramCredential,
    NotificationRecipient,
    PlatformMetricSnapshot,
    ScheduledPost,
    Video,
    XCredential,
    YouTubeCredential,
    Subscription,
    SubscriptionPayment,
    SubscriptionPlan,
)
from .youtube_analytics_service import get_youtube_dashboard_analytics
from .subscriptions import video_usage

User = get_user_model()


def _date_range(request):
    choices = {"today": 1, "7d": 7, "30d": 30, "3m": 90, "1y": 365}
    selected = request.GET.get("period", "30d")
    days = choices.get(selected, 30)
    today = timezone.localdate()
    return selected, today - timedelta(days=days - 1), today


def _latest_metrics(user_id=None):
    query = PlatformMetricSnapshot.objects.select_related("user").order_by(
        "user_id", "platform", "account_key", "-snapshot_date"
    )
    if user_id:
        query = query.filter(user_id=user_id)
    latest, previous = {}, {}
    for row in query:
        key = (row.user_id, row.platform, row.account_key)
        if key not in latest:
            latest[key] = row
        elif key not in previous:
            previous[key] = row
    return latest, previous


def _growth_rows(user_id=None):
    latest, previous = _latest_metrics(user_id)
    grouped = defaultdict(lambda: {"views": 0, "followers": 0, "compared_followers": 0, "previous_followers": 0, "growth": 0, "accounts": 0, "has_follower_baseline": False})
    for key, current in latest.items():
        uid = current.user_id
        row = grouped[uid]
        row["views"] += current.total_views or 0
        row["followers"] += current.followers or 0
        row["accounts"] += 1
        prior = previous.get(key)
        if prior and current.followers is not None and prior.followers is not None:
            row["compared_followers"] += current.followers
            row["previous_followers"] += prior.followers
            row["growth"] += current.followers - prior.followers
            row["has_follower_baseline"] = True
    users = {u.id: u for u in User.objects.filter(id__in=grouped).select_related("settings")}
    published = dict(ScheduledPost.objects.filter(user_id__in=grouped, status="published").values_list("user_id").annotate(total=Count("id")))
    result = []
    for uid, metrics in grouped.items():
        user = users.get(uid)
        if not user:
            continue
        baseline = metrics["previous_followers"]
        metrics["growth_percent"] = round(metrics["growth"] * 100 / baseline, 1) if baseline else None
        metrics["published"] = published.get(uid, 0)
        metrics["user"] = user
        result.append(metrics)
    result.sort(key=lambda row: (row["growth"] if row["has_follower_baseline"] else -10**30), reverse=True)
    return result


def _activity_events(user_id=None, limit=18):
    events = []
    users = User.objects.all()
    videos = Video.objects.select_related("user")
    credentials = []
    posts = ScheduledPost.objects.select_related("user")
    notifications = AdminNotification.objects.select_related("created_by")
    if user_id:
        users = users.filter(id=user_id)
        videos = videos.filter(user_id=user_id)
        credentials = [
            ("YouTube connected", YouTubeCredential.objects.filter(user_id=user_id)),
            ("Instagram connected", InstagramCredential.objects.filter(user_id=user_id)),
            ("Facebook connected", FacebookCredential.objects.filter(user_id=user_id)),
            ("X connected", XCredential.objects.filter(owner_id=user_id)),
        ]
        posts = posts.filter(user_id=user_id)
        notifications = notifications.none()
    else:
        credentials = [
            ("YouTube connected", YouTubeCredential.objects.select_related("user")),
            ("Instagram connected", InstagramCredential.objects.select_related("user")),
            ("Facebook connected", FacebookCredential.objects.select_related("user")),
            ("X connected", XCredential.objects.select_related("owner")),
        ]
    for user in users.order_by("-date_joined")[:12]:
        events.append({"at": user.date_joined, "label": "New user registered", "subject": user.username, "kind": "user"})
    for video in videos.order_by("-created_at")[:20]:
        owner = video.user.username if video.user else "Deleted user"
        events.append({"at": video.created_at, "label": "Video uploaded", "subject": f"{owner} · Video #{video.id}", "kind": "video"})
        if video.status == "completed":
            events.append({"at": video.updated_at, "label": "Video processed", "subject": f"{owner} · Video #{video.id}", "kind": "success"})
    for label, queryset in credentials:
        owner_field = "owner" if label == "X connected" else "user"
        for account in queryset.order_by("-created_at")[:12]:
            owner = getattr(account, owner_field, None)
            if owner:
                events.append({"at": account.created_at, "label": label, "subject": f"{owner.username} · {getattr(account, 'channel_name', None) or getattr(account, 'account_name', None) or getattr(account, 'page_name', None) or getattr(account, 'username', 'Account')}", "kind": "account"})
    for post in posts.order_by("-updated_at")[:20]:
        owner = post.user.username if post.user else "Deleted user"
        label = {"scheduled": "Video scheduled", "published": "Video published", "failed": "Publishing failed"}.get(post.status, "Publishing activity")
        events.append({"at": post.updated_at, "label": label, "subject": f"{owner} · {post.platform.title()} · {post.title or 'Video'}", "kind": "post"})
    if not user_id:
        for notification in notifications.filter(is_active=True, scheduled_at__lte=timezone.now()).order_by("-scheduled_at")[:10]:
            events.append({"at": notification.scheduled_at, "label": "Notification sent", "subject": notification.title, "kind": "notice"})
    return sorted(events, key=lambda item: item["at"], reverse=True)[:limit]


@staff_member_required(login_url="admin:login")
def dashboard(request):
    period, start, end = _date_range(request)
    latest, _ = _latest_metrics()
    totals = {"views": 0, "followers": 0, "accounts": 0, "captured_at": None}
    for row in latest.values():
        totals["views"] += row.total_views or 0
        totals["followers"] += row.followers or 0
        totals["accounts"] += 1
        totals["captured_at"] = max(totals["captured_at"], row.captured_at) if totals["captured_at"] else row.captured_at
    videos = Video.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
    users = User.objects.filter(date_joined__date__gte=start, date_joined__date__lte=end)
    posts = ScheduledPost.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
    days = [start + timedelta(days=index) for index in range((end - start).days + 1)]

    def daily_values(queryset, field):
        rows = queryset.annotate(day=TruncDate(field)).values("day").annotate(count=Count("id"))
        mapping = {row["day"]: row["count"] for row in rows}
        return [{"label": day.strftime("%b %d"), "value": mapping.get(day, 0)} for day in days]

    chart_data = {
        "users": daily_values(users, "date_joined"),
        "uploads": daily_values(videos, "created_at"),
        "processed": daily_values(videos.filter(status="completed"), "updated_at"),
        "published": daily_values(posts.filter(status="published"), "updated_at"),
    }
    new_subscriptions = Subscription.objects.filter(
        starts_at__date__gte=start, starts_at__date__lte=end, is_grandfathered=False,
    ).annotate(day=TruncDate("starts_at")).values("day").annotate(total=Count("id"))
    subscription_by_day = {row["day"]: row["total"] for row in new_subscriptions}
    revenue_by_day = SubscriptionPayment.objects.filter(
        status="captured", payment_date__date__gte=start, payment_date__date__lte=end,
    ).annotate(day=TruncDate("payment_date")).values("day").annotate(total=Sum("amount"))
    revenue_day_map = {row["day"]: row["total"] or 0 for row in revenue_by_day}
    subscription_growth_data = [{"label": day.strftime("%b %d"), "value": subscription_by_day.get(day, 0)} for day in days]
    revenue_growth_data = [{"label": day.strftime("%b %d"), "value": float(revenue_day_map.get(day, 0))} for day in days]
    snapshot_rows = PlatformMetricSnapshot.objects.filter(
        snapshot_date__gte=start, snapshot_date__lte=end
    ).values("snapshot_date").annotate(views=Sum("total_views"), followers=Sum("followers"))
    snapshot_by_day = {row["snapshot_date"]: row for row in snapshot_rows}
    chart_data["views"] = [{"label": day.strftime("%b %d"), "value": snapshot_by_day.get(day, {}).get("views") or 0} for day in days]
    chart_data["followers"] = [{"label": day.strftime("%b %d"), "value": snapshot_by_day.get(day, {}).get("followers") or 0} for day in days]
    platform_performance = list(ScheduledPost.objects.values("platform").annotate(total=Count("id"), published=Count("id", filter=Q(status="published"))).order_by("platform"))
    post_counts = ScheduledPost.objects.filter(status="scheduled", scheduled_at__gte=timezone.now()).count()
    connected_accounts = sum(qs.count() for qs in (
        YouTubeCredential.objects.filter(is_active=True), InstagramCredential.objects.filter(is_active=True),
        FacebookCredential.objects.filter(is_active=True), XCredential.objects.filter(is_active=True),
    ))
    subscription_rows = Subscription.objects.select_related("plan")
    active_paid = subscription_rows.filter(status="active", is_grandfathered=False, starts_at__lte=timezone.now(), expires_at__gt=timezone.now())
    plan_distribution = list(active_paid.values("plan__name").annotate(total=Count("id")).order_by("plan__name"))
    monthly_revenue = SubscriptionPayment.objects.filter(status="captured", payment_date__date__gte=timezone.localdate().replace(day=1)).aggregate(total=Sum("amount"))["total"] or 0
    return render(request, "admin_panel/dashboard.html", {
        "admin_active": "analytics" if request.path.endswith("/analytics/") else "dashboard",
        "page_title": "Analytics" if request.path.endswith("/analytics/") else "Admin overview", "period": period,
        "total_users": User.objects.count(), "active_users": User.objects.filter(is_active=True).count(),
        "total_videos": Video.objects.count(), "processed_videos": Video.objects.filter(status="completed").count(),
        "processing_videos": Video.objects.filter(status="processing").count(), "scheduled_videos": post_counts,
        "total_views": totals["views"], "total_followers": totals["followers"], "connected_accounts": connected_accounts,
        "snapshot_accounts": totals["accounts"], "metrics_captured_at": totals["captured_at"],
        "top_growing_users": _growth_rows()[:6], "chart_data": chart_data,
        "platform_performance": platform_performance, "recent_activity": _activity_events(),
        "unread_notifications": NotificationRecipient.objects.filter(notification__is_active=True, notification__scheduled_at__lte=timezone.now(), read_at__isnull=True).count(),
        "sent_notifications": AdminNotification.objects.filter(is_active=True, scheduled_at__lte=timezone.now()).count(),
        "read_notifications": NotificationRecipient.objects.filter(notification__is_active=True, notification__scheduled_at__lte=timezone.now(), read_at__isnull=False).count(),
        "total_subscribers": active_paid.count(),
        "trial_users": subscription_rows.filter(status="trial", starts_at__lte=timezone.now(), expires_at__gt=timezone.now()).count(),
        "active_monthly": active_paid.filter(plan__code="monthly").count(),
        "active_six_month": active_paid.filter(plan__code="six_months").count(),
        "active_yearly": active_paid.filter(plan__code="yearly").count(),
        "expired_subscriptions": subscription_rows.filter(status="expired").count(),
        "revenue": SubscriptionPayment.objects.filter(status="captured").aggregate(total=Sum("amount"))["total"] or 0,
        "monthly_revenue": monthly_revenue,
        "upcoming_expirations": subscription_rows.filter(status__in=("active", "trial"), expires_at__gte=timezone.now(), expires_at__lte=timezone.now() + timedelta(days=7)).count(),
        "subscription_growth_data": subscription_growth_data,
        "revenue_growth_data": revenue_growth_data,
        "plan_distribution": plan_distribution,
    })


@staff_member_required(login_url="admin:login")
def content_list(request, category="videos"):
    if category in ("scheduled", "published"):
        posts = ScheduledPost.objects.select_related("user", "video").order_by("-scheduled_at")
        posts = posts.filter(status="scheduled") if category == "scheduled" else posts.filter(status="published")
        context = {"items": posts[:250], "content_kind": "posts"}
    else:
        videos = Video.objects.select_related("user").order_by("-created_at")
        if category == "processing":
            videos = videos.filter(status="processing")
        elif category == "failed":
            videos = videos.filter(status="failed")
        context = {"items": videos[:250], "content_kind": "videos"}
    labels = {"videos": "All videos", "processing": "Processing videos", "scheduled": "Scheduled videos", "published": "Published videos", "failed": "Failed videos"}
    if category not in labels:
        raise Http404
    context.update({"admin_active": category, "page_title": labels[category], "category": category})
    return render(request, "admin_panel/content.html", context)


@staff_member_required(login_url="admin:login")
def users_list(request):
    query = request.GET.get("q", "").strip()
    users = User.objects.order_by("-date_joined")
    if query:
        users = users.filter(username__icontains=query) | User.objects.filter(email__icontains=query)
    page = Paginator(users.distinct(), 50).get_page(request.GET.get("page"))
    ids = [user.id for user in page.object_list]
    count_maps = {}
    for label, qs in (
        ("videos", Video.objects.filter(user_id__in=ids)),
        ("youtube", YouTubeCredential.objects.filter(user_id__in=ids, is_active=True)),
        ("instagram", InstagramCredential.objects.filter(user_id__in=ids, is_active=True)),
        ("facebook", FacebookCredential.objects.filter(user_id__in=ids, is_active=True)),
        ("x", XCredential.objects.filter(owner_id__in=ids, is_active=True)),
    ):
        count_maps[label] = dict(qs.values_list(qs.model._meta.get_field("user" if label != "x" else "owner").attname).annotate(total=Count("id")))
    growth = {row["user"].id: row for row in _growth_rows()}
    for user in page.object_list:
        user.admin_video_count = count_maps["videos"].get(user.id, 0)
        user.admin_account_count = sum(count_maps[key].get(user.id, 0) for key in ("youtube", "instagram", "facebook", "x"))
        user.admin_growth = growth.get(user.id)
        try:
            user.admin_avatar = user.settings.profile_picture.url
        except (ObjectDoesNotExist, ValueError):
            user.admin_avatar = ""
    return render(request, "admin_panel/users.html", {"admin_active": "users", "page_title": "Users", "page": page, "query": query})


@staff_member_required(login_url="admin:login")
def user_detail(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if request.method == "POST":
        user.first_name = request.POST.get("first_name", "")[:150]
        user.last_name = request.POST.get("last_name", "")[:150]
        user.email = request.POST.get("email", "")[:254]
        user.is_active = request.POST.get("is_active") == "on"
        user.save(update_fields=("first_name", "last_name", "email", "is_active"))
        messages.success(request, "User profile saved.")
        return redirect("admin_panel_user_detail", user_id=user.id)
    snapshots = list(PlatformMetricSnapshot.objects.filter(user=user).order_by("-snapshot_date", "platform", "account_key"))
    latest, previous = _latest_metrics(user.id)
    metrics = _growth_rows(user.id)
    metric_summary = metrics[0] if metrics else None
    return render(request, "admin_panel/user_detail.html", {
        "admin_active": "users", "page_title": user.get_full_name() or user.username, "target_user": user,
        "videos": Video.objects.filter(user=user).order_by("-created_at")[:12],
        "scheduled_posts": ScheduledPost.objects.filter(user=user, status="scheduled").count(),
        "published_posts": ScheduledPost.objects.filter(user=user, status="published").count(),
        "connected_accounts": sum(qs.count() for qs in (
            YouTubeCredential.objects.filter(user=user, is_active=True), InstagramCredential.objects.filter(user=user, is_active=True),
            FacebookCredential.objects.filter(user=user, is_active=True), XCredential.objects.filter(owner=user, is_active=True),
        )), "snapshots": snapshots[:24], "metrics": metric_summary,
        "activity": _activity_events(user.id, 20),
        "platform_performance": ScheduledPost.objects.filter(user=user).values("platform").annotate(total=Count("id"), published=Count("id", filter=Q(status="published"))),
    })


@staff_member_required(login_url="admin:login")
@require_POST
def user_action(request, user_id, action):
    target = get_object_or_404(User, pk=user_id)
    if action == "delete":
        if target.pk == request.user.pk:
            messages.error(request, "You cannot delete your own administrator account here.")
            return redirect("admin_panel_users")
        target.delete()
        messages.success(request, "User and their app data were deleted.")
        return redirect("admin_panel_users")
    if action not in ("enable", "disable"):
        raise Http404
    target.is_active = action == "enable"
    target.save(update_fields=("is_active",))
    messages.success(request, f"{target.username} was enabled." if action == "enable" else f"{target.username} was disabled.")
    return redirect("admin_panel_user_detail", user_id=target.id)


@staff_member_required(login_url="admin:login")
def social_accounts(request):
    return render(request, "admin_panel/social_accounts.html", {
        "admin_active": "social", "page_title": "Social accounts",
        "youtube_accounts": YouTubeCredential.objects.select_related("user").order_by("-created_at")[:300],
        "instagram_accounts": InstagramCredential.objects.select_related("user").order_by("-created_at")[:300],
        "facebook_accounts": FacebookCredential.objects.select_related("user").order_by("-created_at")[:300],
        "x_accounts": XCredential.objects.select_related("owner").order_by("-created_at")[:300],
    })


@staff_member_required(login_url="admin:login")
@require_POST
def refresh_user_metrics(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    today = timezone.localdate()
    saved = 0
    yt_credentials = YouTubeCredential.objects.filter(user=user, is_active=True)
    yt_result = get_youtube_dashboard_analytics(yt_credentials)
    yt_by_id = {item.get("credential_id"): item for item in yt_result.get("accounts", [])}
    for credential in yt_credentials:
        item = yt_by_id.get(credential.id, {})
        if item.get("available"):
            PlatformMetricSnapshot.objects.update_or_create(
                user=user, platform="youtube", account_key=credential.channel_id or str(credential.id), snapshot_date=today,
                defaults={"account_name": item.get("account_name") or credential.channel_name, "total_views": item.get("total_channel_views"), "followers": item.get("subscribers")},
            )
            saved += 1
    fb_credentials = FacebookCredential.objects.filter(user=user, is_active=True)
    fb_result = get_facebook_dashboard_analytics(fb_credentials)
    fb_by_id = {item.get("credential_id"): item for item in fb_result.get("accounts", [])}
    for credential in fb_credentials:
        item = fb_by_id.get(credential.id, {})
        if item.get("available"):
            PlatformMetricSnapshot.objects.update_or_create(
                user=user, platform="facebook", account_key=credential.page_id or str(credential.id), snapshot_date=today,
                defaults={"account_name": item.get("account_name") or credential.page_name, "total_views": None, "followers": item.get("followers")},
            )
            saved += 1
    if saved:
        messages.success(request, f"Saved {saved} verified account snapshot(s) for today. Growth appears after a later snapshot date.")
    else:
        messages.warning(request, "No real analytics were available. Reauthorize accounts with analytics permissions and try again.")
    return redirect("admin_panel_user_detail", user_id=user.id)


@staff_member_required(login_url="admin:login")
def notifications(request):
    queryset = AdminNotification.objects.annotate(
        recipient_count=Count("recipients", distinct=True),
        read_count=Count("recipients", filter=Q(recipients__read_at__isnull=False), distinct=True),
    ).order_by("-created_at")
    return render(request, "admin_panel/notifications.html", {
        "admin_active": "notifications", "page_title": "Notifications", "notifications": queryset[:200],
        "total_sent": queryset.filter(is_active=True, scheduled_at__lte=timezone.now()).count(),
        "read_count": NotificationRecipient.objects.filter(notification__is_active=True, notification__scheduled_at__lte=timezone.now(), read_at__isnull=False).count(),
        "unread_count": NotificationRecipient.objects.filter(notification__is_active=True, notification__scheduled_at__lte=timezone.now(), read_at__isnull=True).count(),
    })


@staff_member_required(login_url="admin:login")
def notification_form(request, notification_id=None):
    instance = get_object_or_404(AdminNotification, pk=notification_id) if notification_id else None
    initial = {}
    if instance:
        initial = {"title": instance.title, "message": instance.message, "notification_type": instance.notification_type, "priority": instance.priority, "target": instance.target, "image": instance.image, "scheduled_at": timezone.localtime(instance.scheduled_at).strftime("%Y-%m-%dT%H:%M"), "is_active": instance.is_active, "users": instance.users.all()}
    form = AdminNotificationForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        notice = instance or AdminNotification(created_by=request.user)
        for field in ("title", "message", "notification_type", "priority", "target", "scheduled_at", "is_active"):
            value = form.cleaned_data.get(field)
            if field == "scheduled_at":
                value = value or timezone.now()
            setattr(notice, field, value)
        if form.cleaned_data.get("image"):
            notice.image = form.cleaned_data["image"]
        notice.save()
        selected = form.cleaned_data["users"] if notice.target == "selected" else (User.objects.filter(is_active=True) if notice.target == "active" else User.objects.all())
        desired_ids = set(selected.values_list("id", flat=True))
        existing_ids = set(notice.recipients.values_list("user_id", flat=True))
        notice.recipients.exclude(user_id__in=desired_ids).delete()
        NotificationRecipient.objects.bulk_create(
            [NotificationRecipient(notification=notice, user_id=uid) for uid in desired_ids - existing_ids],
            ignore_conflicts=True,
        )
        messages.success(request, "Notification saved. It will appear in recipient inboxes at its scheduled time while active.")
        return redirect("admin_panel_notifications")
    return render(request, "admin_panel/notification_form.html", {"admin_active": "notifications", "page_title": "Edit notification" if instance else "Create notification", "form": form, "instance": instance})


@staff_member_required(login_url="admin:login")
@require_POST
def notification_action(request, notification_id, action):
    notice = get_object_or_404(AdminNotification, pk=notification_id)
    if action == "delete":
        notice.delete()
        messages.success(request, "Notification deleted.")
    elif action == "toggle":
        notice.is_active = not notice.is_active
        notice.save(update_fields=("is_active",))
        messages.success(request, "Notification status updated.")
    else:
        raise Http404
    return redirect("admin_panel_notifications")


@staff_member_required(login_url="admin:login")
def music_management(request):
    music = BackgroundMusic.objects.select_related("user").order_by("-is_default", "-created_at")
    forms = {item.id: AdminMusicForm(instance=item, prefix=f"music-{item.id}") for item in music}
    return render(request, "admin_panel/music.html", {"admin_active": "music", "page_title": "Background music", "music": music, "music_forms": forms, "new_form": AdminMusicForm(prefix="new")})


@staff_member_required(login_url="admin:login")
@require_POST
def music_action(request, action, music_id=None):
    item = get_object_or_404(BackgroundMusic, pk=music_id) if music_id else None
    if action == "delete" and item:
        item.audio_file.delete(save=False)
        item.delete()
        messages.success(request, "Music track deleted.")
        return redirect("admin_panel_music")
    if action == "toggle" and item:
        item.is_active = not item.is_active
        item.save(update_fields=("is_active", "updated_at"))
        messages.success(request, "Music availability updated.")
        return redirect("admin_panel_music")
    if action not in ("save", "create"):
        raise Http404
    form = AdminMusicForm(request.POST, request.FILES, instance=item, prefix=f"music-{item.id}" if item else "new")
    if form.is_valid() and (item or form.cleaned_data.get("audio_file")):
        music = form.save(commit=False)
        if not item:
            music.user = None
            music.is_shared = True
        if music.is_default:
            BackgroundMusic.objects.exclude(pk=music.pk).update(is_default=False)
            music.user = None
            music.is_shared = True
        music.save()
        if music.is_default:
            BackgroundMusic.objects.filter(pk=music.pk).update(user=None, is_shared=True)
        messages.success(request, "Music track saved.")
    else:
        messages.error(request, "Check the track name and provide an audio file for new tracks.")
    return redirect("admin_panel_music")


@staff_member_required(login_url="admin:login")
def advertisement_management(request):
    ads = Advertisement.objects.order_by("-created_at")
    forms = {item.id: AdminAdvertisementForm(instance=item, prefix=f"ad-{item.id}") for item in ads}
    return render(request, "admin_panel/advertisements.html", {"admin_active": "ads", "page_title": "Advertisements", "advertisements": ads, "ad_forms": forms, "new_form": AdminAdvertisementForm(prefix="new")})


@staff_member_required(login_url="admin:login")
@require_POST
def advertisement_action(request, action, ad_id=None):
    ad = get_object_or_404(Advertisement, pk=ad_id) if ad_id else None
    if action == "delete" and ad:
        ad.media_file.delete(save=False)
        ad.delete()
        messages.success(request, "Advertisement deleted.")
        return redirect("admin_panel_ads")
    if action == "toggle" and ad:
        ad.is_active = not ad.is_active
        ad.save(update_fields=("is_active", "updated_at"))
        messages.success(request, "Advertisement status updated.")
        return redirect("admin_panel_ads")
    if action not in ("save", "create"):
        raise Http404
    form = AdminAdvertisementForm(request.POST, request.FILES, instance=ad, prefix=f"ad-{ad.id}" if ad else "new")
    if form.is_valid() and (ad or form.cleaned_data.get("media_file")):
        form.save()
        messages.success(request, "Advertisement settings saved.")
    else:
        messages.error(request, "Select a valid media file and check the ad settings.")
    return redirect("admin_panel_ads")


@login_required(login_url="login")
def user_notifications(request):
    inbox = NotificationRecipient.objects.filter(
        user=request.user, notification__is_active=True, notification__scheduled_at__lte=timezone.now()
    ).select_related("notification").order_by("read_at", "-notification__scheduled_at")[:100]
    return render(request, "admin_panel/user_notifications.html", {"inbox": inbox, "page_title": "Notifications"})


@login_required(login_url="login")
@require_POST
def notification_read(request, recipient_id):
    recipient = get_object_or_404(NotificationRecipient, pk=recipient_id, user=request.user)
    if not recipient.read_at:
        recipient.read_at = timezone.now()
        recipient.save(update_fields=("read_at",))
    return redirect("user_notifications")


@staff_member_required(login_url="admin:login")
def growth_page(request):
    return render(request, "admin_panel/growth.html", {
        "admin_active": "growth", "page_title": "User growth", "users": _growth_rows(),
    })


@staff_member_required(login_url="admin:login")
def progress_reports(request):
    return render(request, "admin_panel/growth.html", {
        "admin_active": "progress_reports", "page_title": "Progress reports", "users": _growth_rows(),
    })


@staff_member_required(login_url="admin:login")
def subscriptions_management(request):
    subscriptions = Subscription.objects.select_related("user", "plan").order_by("-updated_at")
    rows = []
    for subscription in subscriptions[:500]:
        rows.append({
            "subscription": subscription,
            "videos_used": video_usage(subscription.user, subscription),
            "accounts": {
                "YouTube": YouTubeCredential.objects.filter(user=subscription.user, is_active=True).count(),
                "Instagram": InstagramCredential.objects.filter(user=subscription.user, is_active=True).count(),
                "Facebook": FacebookCredential.objects.filter(user=subscription.user, is_active=True).count(),
                "X": XCredential.objects.filter(owner=subscription.user, is_active=True).count(),
            },
            "last_payment": SubscriptionPayment.objects.filter(user=subscription.user, status="captured").order_by("-payment_date").first(),
        })
    return render(request, "admin_panel/subscriptions.html", {
        "admin_active": "subscriptions", "page_title": "Subscriptions",
        "rows": rows, "plans": SubscriptionPlan.objects.filter(is_active=True),
    })


@staff_member_required(login_url="admin:login")
@require_POST
def subscription_action(request, subscription_id):
    subscription = get_object_or_404(Subscription.objects.select_related("user", "plan"), pk=subscription_id)
    action = request.POST.get("action")
    if action == "extend":
        try:
            days = int(request.POST.get("days", ""))
        except (TypeError, ValueError):
            days = 0
        if not 1 <= days <= 3650:
            messages.error(request, "Enter an extension from 1 to 3650 days.")
        else:
            anchor = max(timezone.now(), subscription.expires_at) if subscription.expires_at else timezone.now()
            subscription.expires_at = anchor + timedelta(days=days)
            subscription.status = "active" if subscription.plan and not subscription.plan.is_trial else "trial"
            subscription.save(update_fields=("expires_at", "status", "updated_at"))
            messages.success(request, f"{subscription.user.username}'s subscription was extended by {days} days.")
    elif action in ("cancel", "deactivate"):
        subscription.status = "cancelled" if action == "cancel" else "expired"
        if action == "deactivate":
            subscription.expires_at = timezone.now()
        subscription.save(update_fields=("status", "expires_at", "updated_at"))
        messages.success(request, f"{subscription.user.username}'s subscription was {subscription.status}.")
    elif action == "change_plan":
        plan = get_object_or_404(SubscriptionPlan, pk=request.POST.get("plan_id"), is_active=True)
        now = timezone.now()
        subscription.plan = plan
        subscription.status = "trial" if plan.is_trial else "active"
        subscription.starts_at = now
        subscription.expires_at = now + timedelta(days=plan.duration_days)
        subscription.is_grandfathered = False
        subscription.save(update_fields=("plan", "status", "starts_at", "expires_at", "is_grandfathered", "updated_at"))
        messages.success(request, f"{subscription.user.username}'s plan was changed to {plan.name}.")
    else:
        raise Http404
    return redirect("admin_panel_subscriptions")


@staff_member_required(login_url="admin:login")
def subscription_plans(request):
    if request.method == "POST":
        plan = get_object_or_404(SubscriptionPlan, pk=request.POST.get("plan_id"))
        form = SubscriptionPlanForm(request.POST, instance=plan, prefix=f"plan-{plan.pk}")
        if form.is_valid():
            form.save()
            messages.success(request, f"{plan.name} settings saved.")
            return redirect("admin_panel_plans")
        messages.error(request, "Check the plan fields and features JSON.")
    plans = SubscriptionPlan.objects.order_by("sort_order", "price")
    forms = [(plan, SubscriptionPlanForm(instance=plan, prefix=f"plan-{plan.pk}")) for plan in plans]
    return render(request, "admin_panel/plans.html", {
        "admin_active": "plans", "page_title": "Subscription plans", "plans": forms,
    })
