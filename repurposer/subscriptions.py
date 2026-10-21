"""Subscription entitlement and Razorpay verification helpers."""
import hashlib
import hmac
from datetime import timedelta
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Subscription, SubscriptionPayment, SubscriptionPlan


def current_subscription(user):
    now = timezone.now()
    current = Subscription.objects.filter(user=user, status__in=("trial", "active"), starts_at__lte=now).filter(
        expires_at__isnull=True
    ) | Subscription.objects.filter(user=user, status__in=("trial", "active"), starts_at__lte=now, expires_at__gt=now)
    sub = current.select_related("plan").order_by("-starts_at", "-pk").first()
    if not sub:
        sub = Subscription.objects.filter(user=user).select_related("plan").order_by("-starts_at", "-pk").first()
    if not sub:
        return None
    if sub.is_grandfathered:
        return sub
    if sub.expires_at and sub.expires_at <= timezone.now() and sub.status in ("trial", "active"):
        Subscription.objects.filter(pk=sub.pk).update(status="expired")
        sub.status = "expired"
    return sub


def video_usage(user, subscription=None):
    sub = subscription or current_subscription(user)
    if not sub:
        return 0
    return sub.videos_used


def video_entitlement(user):
    sub = current_subscription(user)
    if sub and sub.is_grandfathered:
        return True, sub, None, None
    if not sub or sub.status not in ("trial", "active") or not sub.plan:
        return False, sub, 0, 0
    if not plan_has_feature(sub, "video_processing"):
        return False, sub, sub.videos_used, 0
    used = video_usage(user, sub)
    if sub.starts_at > timezone.now():
        return False, sub, used, 0
    limit = sub.plan.video_limit
    if sub.plan.is_trial and sub.expires_at and sub.expires_at <= timezone.now():
        return False, sub, used, 0
    if limit is not None and used >= limit:
        return False, sub, used, max(0, limit - used)
    return True, sub, used, None if limit is None else limit - used


def account_entitlement(user, platform, existing=False):
    """Check a per-platform account allowance; existing reconnects are allowed."""
    sub = current_subscription(user)
    if sub and sub.is_grandfathered:
        return True, None
    if not sub or sub.status not in ("trial", "active") or not sub.plan:
        return False, "An active subscription is required to connect social accounts."
    if sub.starts_at > timezone.now():
        return False, "Your next plan starts after the current subscription ends."
    if not plan_has_feature(sub, platform):
        return False, f"{platform.title()} is not included in your current plan. Upgrade to enable it."
    if existing:
        return True, None
    limit = sub.plan.account_limit
    if limit is None:
        return True, None
    from .models import FacebookCredential, InstagramCredential, XCredential, YouTubeCredential
    models = {"youtube": (YouTubeCredential, "user"), "instagram": (InstagramCredential, "user"), "facebook": (FacebookCredential, "user"), "x": (XCredential, "owner")}
    model, owner_field = models[platform]
    count = model.objects.filter(**{owner_field: user, "is_active": True}).count()
    labels = {"youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook", "x": "X"}
    label = labels[platform]
    return (True, None) if count < limit else (False, f"You have reached the limit of {limit} {label} account(s) on your plan. Upgrade to connect more.")


def plan_has_feature(subscription, feature):
    if subscription and subscription.is_grandfathered:
        return True
    return bool(subscription and subscription.plan and feature in (subscription.plan.features or []))


def subscription_allows(user, feature):
    sub = current_subscription(user)
    if sub and sub.is_grandfathered:
        return True
    return bool(
        sub and sub.status in ("trial", "active") and sub.plan
        and sub.starts_at <= timezone.now()
        and (not sub.expires_at or sub.expires_at > timezone.now())
        and plan_has_feature(sub, feature)
    )


def razorpay_configured():
    key_id = getattr(settings, "RAZORPAY_KEY_ID", "")
    secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
    mode = getattr(settings, "RAZORPAY_MODE", "test")
    expected_prefix = "rzp_live_" if mode == "live" else "rzp_test_"
    return bool(key_id.startswith(expected_prefix) and secret and mode in ("test", "live"))


def create_razorpay_order(user, plan):
    if not razorpay_configured():
        raise RuntimeError("Payments are not configured yet. Please contact the administrator.")
    amount = int(plan.price * Decimal("100"))
    if amount <= 0:
        raise ValueError("Paid checkout requires a plan price greater than zero.")
    response = requests.post(
        "https://api.razorpay.com/v1/orders",
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
        json={"amount": amount, "currency": plan.currency, "receipt": f"sub-{user.pk}-{timezone.now().timestamp():.0f}"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    SubscriptionPayment.objects.create(user=user, plan=plan, order_id=data["id"], amount=plan.price, currency=plan.currency, status="created", gateway_data={"mode": getattr(settings, "RAZORPAY_MODE", "test")})
    return data


@transaction.atomic
def verify_razorpay_payment(user, order_id, payment_id, signature):
    payment = SubscriptionPayment.objects.select_for_update().select_related("plan").get(user=user, order_id=order_id)
    secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
    expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(expected, signature):
        raise ValueError("Payment signature could not be verified.")
    if payment.status == "captured":
        return payment
    if SubscriptionPayment.objects.filter(transaction_id=payment_id).exclude(pk=payment.pk).exists():
        raise ValueError("This payment has already been applied to another order.")
    response = requests.get(f"https://api.razorpay.com/v1/payments/{payment_id}", auth=(settings.RAZORPAY_KEY_ID, secret), timeout=20)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "captured" or data.get("order_id") != payment.order_id or data.get("amount") != int(payment.amount * Decimal("100")) or data.get("currency") != payment.currency:
        payment.status = "failed" if data.get("status") == "failed" else "pending"
        payment.gateway_data = data
        payment.save(update_fields=("status", "gateway_data"))
        return payment
    return activate_captured_payment(payment, payment_id, data)


@transaction.atomic
def activate_captured_payment(payment, payment_id, data):
    """Idempotently activate only a payment proven captured by Razorpay."""
    payment = SubscriptionPayment.objects.select_for_update().select_related("plan", "user").get(pk=payment.pk)
    if payment.status == "captured":
        return payment
    if SubscriptionPayment.objects.filter(transaction_id=payment_id).exclude(pk=payment.pk).exists():
        raise ValueError("This payment has already been applied to another order.")
    now = timezone.now()
    user = payment.user
    previous = current_subscription(user)
    latest_paid_expiry = Subscription.objects.filter(
        user=user, status="active", is_grandfathered=False, expires_at__gt=now
    ).aggregate(latest=Max("expires_at"))["latest"]
    starts = max(now, latest_paid_expiry) if latest_paid_expiry else now
    expires = starts + timedelta(days=payment.plan.duration_days)
    subscription = Subscription.objects.create(user=user, plan=payment.plan, status="active", starts_at=starts, expires_at=expires, is_grandfathered=False, renewal_enabled=False)
    payment.transaction_id = payment_id
    payment.status = "captured"
    payment.payment_date = now
    payment.subscription = subscription
    payment.subscription_starts_at = starts
    payment.subscription_expires_at = expires
    payment.gateway_data = {"status": data.get("status"), "order_id": data.get("order_id"), "amount": data.get("amount"), "currency": data.get("currency"), "source": data.get("source", "checkout")}
    payment.save()
    return payment
