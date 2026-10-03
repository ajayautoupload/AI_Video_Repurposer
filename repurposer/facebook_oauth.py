import json
import secrets
from urllib.parse import urlencode

import requests

from django.conf import settings


FACEBOOK_GRAPH_VERSION = "v23.0"


def create_facebook_oauth_state():
    """
    Create a secure random OAuth state value.
    """

    return secrets.token_urlsafe(32)


def get_facebook_authorization_url(state):
    """
    Create the Facebook Login for Business URL
    for Instagram API onboarding.

    The Facebook Login for Business configuration
    created in Meta Developer Dashboard is explicitly
    passed using config_id.
    """

    extras = json.dumps(
        {
            "setup": {
                "channel": "IG_API_ONBOARDING"
            }
        },
        separators=(",", ":"),
    )

    scopes = [
       "instagram_basic",
    "instagram_content_publish",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "business_management",
    ]

    params = {
        "client_id": settings.META_APP_ID,

        "config_id": (
            settings.FACEBOOK_LOGIN_CONFIG_ID
        ),

        "display": "page",

        "extras": extras,

        "redirect_uri": (
            settings.FACEBOOK_REDIRECT_URI
        ),

        "response_type": "token",

        "scope": ",".join(scopes),

        "state": state,
    }

    authorization_url = (
        f"https://www.facebook.com/"
        f"{FACEBOOK_GRAPH_VERSION}/dialog/oauth?"
        + urlencode(params)
    )

    return authorization_url


def exchange_facebook_code_for_token(code):
    """
    Legacy authorization-code exchange helper.

    This is kept for compatibility with the existing project.

    The Facebook Login for Business Instagram flow currently
    uses response_type=token, so the browser receives the
    access token in the URL fragment instead of a code.
    """

    response = requests.get(
        f"https://graph.facebook.com/"
        f"{FACEBOOK_GRAPH_VERSION}/oauth/access_token",
        params={
            "client_id": settings.META_APP_ID,

            "client_secret": (
                settings.META_APP_SECRET
            ),

            "redirect_uri": (
                settings.FACEBOOK_REDIRECT_URI
            ),

            "code": code,
        },
        timeout=30,
    )

    if not response.ok:

        try:
            error_data = response.json()

        except ValueError:
            error_data = {
                "raw_response": response.text
            }

        raise ValueError(
            "Facebook token exchange failed: "
            f"{error_data}"
        )

    data = response.json()

    if not data.get("access_token"):

        raise ValueError(
            "Facebook access token was not returned. "
            f"Facebook response: {data}"
        )

    return data


def get_facebook_pages(user_access_token):
    """
    Get Facebook Pages available to the
    authenticated Facebook user.

    Also request the linked Instagram
    Professional Account ID.
    """

    response = requests.get(
        f"https://graph.facebook.com/"
        f"{FACEBOOK_GRAPH_VERSION}/me/accounts",
        params={
            "fields": (
                "id,"
                "name,"
                "access_token,"
                "instagram_business_account"
            ),

            "access_token": user_access_token,
        },

        timeout=30,
    )

    if not response.ok:

        try:
            error_data = response.json()

        except ValueError:
            error_data = {
                "raw_response": response.text
            }

        raise ValueError(
            "Facebook Pages request failed: "
            f"{error_data}"
        )

    data = response.json()

    pages = data.get(
        "data",
        []
    )

    # --------------------------------
    # Normalize Instagram information
    # --------------------------------

    for page in pages:

        instagram_business_account = (
            page.get(
                "instagram_business_account"
            )
        )

        if isinstance(
            instagram_business_account,
            dict,
        ):

            page[
                "instagram_account_id"
            ] = (
                instagram_business_account.get(
                    "id"
                )
            )

        else:

            page[
                "instagram_account_id"
            ] = None

    return pages