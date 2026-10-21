from django.apps import AppConfig


class RepurposerConfig(AppConfig):
    name = 'repurposer'

    def ready(self):
        from . import signals  # noqa: F401
