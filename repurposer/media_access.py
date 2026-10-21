from urllib.parse import quote, urlencode

from django.conf import settings
from django.core.signing import TimestampSigner


def signed_public_media_url(media_path):
    """Build a short-lived public URL for a platform media fetcher."""
    path = str(media_path or "").replace("\\", "/").lstrip("/")
    media_prefix = settings.MEDIA_URL.strip("/") + "/"
    if path.startswith(media_prefix):
        path = path[len(media_prefix):]

    token = TimestampSigner(salt="repurposer.media").sign(path)
    media_url = settings.MEDIA_URL.strip("/")
    return (
        f"{settings.PUBLIC_BASE_URL.rstrip('/')}/"
        f"{media_url}/{quote(path, safe='/')}?"
        f"{urlencode({'token': token})}"
    )
