from django.db.models import Count, Q

from .models import ScheduledPost


def platform_progress(request):
    """Small user-scoped publishing summary for the sidebar and dashboard."""
    if not request.user.is_authenticated:
        return {"platform_progress": []}

    counts = {
        row["platform"]: row
        for row in ScheduledPost.objects.filter(user=request.user)
        .values("platform")
        .annotate(
            total=Count("id"),
            published=Count("id", filter=Q(status="published")),
            scheduled=Count("id", filter=Q(status="scheduled")),
            failed=Count("id", filter=Q(status="failed")),
        )
    }
    return {
        "platform_progress": [
            {
                "key": key,
                "label": label,
                "total": counts.get(key, {}).get("total", 0),
                "published": counts.get(key, {}).get("published", 0),
                "scheduled": counts.get(key, {}).get("scheduled", 0),
                "failed": counts.get(key, {}).get("failed", 0),
            }
            for key, label in ScheduledPost.PLATFORM_CHOICES
        ]
    }
