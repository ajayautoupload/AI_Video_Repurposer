from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import Advertisement, BackgroundMusic, NotificationRecipient, SubscriptionPlan

User = get_user_model()


class AdminNotificationForm(forms.Form):
    title = forms.CharField(max_length=180)
    message = forms.CharField(max_length=5000, widget=forms.Textarea(attrs={"rows": 5}))
    notification_type = forms.ChoiceField(choices=(
        ("announcement", "Announcement"), ("update", "Product update"),
        ("maintenance", "Maintenance"), ("warning", "Important notice"),
    ))
    priority = forms.ChoiceField(choices=(("low", "Low"), ("normal", "Normal"), ("high", "High")))
    target = forms.ChoiceField(choices=(("all", "All users"), ("active", "Active users"), ("selected", "Selected users")))
    users = forms.ModelMultipleChoiceField(queryset=User.objects.all().order_by("username"), required=False, widget=forms.SelectMultiple(attrs={"size": 7}))
    image = forms.FileField(required=False)
    scheduled_at = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    is_active = forms.BooleanField(required=False, initial=True)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("target") == "selected" and not cleaned.get("users"):
            self.add_error("users", "Choose at least one recipient.")
        return cleaned

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and image.content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise ValidationError("Choose a JPEG, PNG, WebP, or GIF image.")
        return image


class AdminMusicForm(forms.ModelForm):
    audio_file = forms.FileField(required=False)

    class Meta:
        model = BackgroundMusic
        fields = ("name", "audio_file", "is_active", "is_default")


class AdminAdvertisementForm(forms.ModelForm):
    media_file = forms.FileField(required=False, label="Image or video")

    class Meta:
        model = Advertisement
        fields = ("media_file", "destination_url", "is_active", "voice_enabled", "duration_seconds", "placement")
        widgets = {"duration_seconds": forms.NumberInput(attrs={"min": 3, "max": 120})}

    def clean_duration_seconds(self):
        value = self.cleaned_data["duration_seconds"]
        if not 3 <= value <= 120:
            raise forms.ValidationError("Choose a display duration from 3 to 120 seconds.")
        return value


class SubscriptionPlanForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPlan
        fields = ("name", "price", "currency", "duration_days", "video_limit", "account_limit", "features", "is_active", "priority_processing", "sort_order")
        widgets = {"features": forms.Textarea(attrs={"rows": 4, "placeholder": '["video_processing", "scheduling"]'})}
