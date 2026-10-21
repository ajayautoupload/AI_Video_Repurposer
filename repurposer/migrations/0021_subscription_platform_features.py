from django.db import migrations


def add_supported_platform_flags(apps, schema_editor):
    Plan = apps.get_model("repurposer", "SubscriptionPlan")
    old_defaults = ["video_processing", "scheduling", "publishing", "analytics", "notifications"]
    new_defaults = old_defaults + ["youtube", "instagram", "facebook", "x"]
    for plan in Plan.objects.all():
        if plan.features == old_defaults:
            plan.features = new_defaults
            plan.save(update_fields=("features",))


class Migration(migrations.Migration):
    dependencies = [("repurposer", "0020_subscription_video_counter")]
    operations = [migrations.RunPython(add_supported_platform_flags, migrations.RunPython.noop)]
