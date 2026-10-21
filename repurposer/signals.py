from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.db.models import F
from django.dispatch import receiver
from django.utils import timezone

from .models import Subscription, SubscriptionPlan, Video


@receiver(post_save, sender=get_user_model())
def start_new_user_trial(sender, instance, created, **kwargs):
    if not created:
        return
    plan = SubscriptionPlan.objects.filter(code="free_trial", is_active=True).first()
    if not plan:
        # Safe fallback for deployments/tests that create users before the plan seed exists.
        plan = SubscriptionPlan.objects.create(code="free_trial", name="Free Trial", price=0, duration_days=30, video_limit=10, account_limit=2, is_trial=True, sort_order=0)
    now = timezone.now()
    Subscription.objects.create(user=instance, plan=plan, status="trial", starts_at=now, expires_at=now + timedelta(days=plan.duration_days))


@receiver(post_save, sender=Video)
def count_accepted_user_video(sender, instance, created, **kwargs):
    if not created or not instance.user_id:
        return
    now = timezone.now()
    entitlement = Subscription.objects.filter(
        user_id=instance.user_id, status__in=("trial", "active"),
        starts_at__lte=now,
    ).filter(expires_at__isnull=True) | Subscription.objects.filter(
        user_id=instance.user_id, status__in=("trial", "active"),
        starts_at__lte=now, expires_at__gt=now,
    )
    current = entitlement.order_by("-starts_at", "-pk").first()
    if current and not current.is_grandfathered:
        Subscription.objects.filter(pk=current.pk).update(videos_used=F("videos_used") + 1)
