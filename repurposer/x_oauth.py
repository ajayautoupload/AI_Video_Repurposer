import base64
import hashlib
import json
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings

from .models import XCredential


# ============================================================
# X OAUTH CONFIGURATION
# ============================================================

X_AUTHORIZE_URL = (
    "https://x.com/i/oauth2/authorize"
)

X_TOKEN_URL = (
    "https://api.x.com/2/oauth2/token"
)

X_USER_URL = (
    "https://api.x.com/2/users/me"
)


X_SCOPES = [
    "tweet.read",
    "tweet.write",
    "users.read",
    "media.write",
    "offline.access",
]


# ============================================================
# PKCE
# ============================================================


def generate_pkce():

    code_verifier = (
        secrets.token_urlsafe(64)
    )

    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(
                code_verifier.encode("utf-8")
            ).digest()
        )
        .decode("utf-8")
        .rstrip("=")
    )

    return (
        code_verifier,
        code_challenge,
    )


# ============================================================
# X AUTHORIZATION URL
# ============================================================


def build_x_authorization_url():

    client_id = (
        settings.X_CLIENT_ID
    )

    if not client_id:

        raise ValueError(
            "X_CLIENT_ID is not configured."
        )

    state = (
        secrets.token_urlsafe(32)
    )

    (
        code_verifier,
        code_challenge,
    ) = generate_pkce()

    params = {

        "response_type": "code",

        "client_id": client_id,

        "redirect_uri": (
            settings.X_REDIRECT_URI
        ),

        "scope": (
            " ".join(X_SCOPES)
        ),

        "state": state,

        "code_challenge": (
            code_challenge
        ),

        "code_challenge_method": "S256",
    }

    authorization_url = (
        f"{X_AUTHORIZE_URL}?"
        f"{urlencode(params)}"
    )

    return {

        "authorization_url": (
            authorization_url
        ),

        "state": state,

        "code_verifier": (
            code_verifier
        ),
    }


# ============================================================
# EXCHANGE AUTHORIZATION CODE FOR TOKEN
# ============================================================


def exchange_code_for_token(
    code,
    code_verifier,
):

    client_id = (
        settings.X_CLIENT_ID
    )

    client_secret = (
        settings.X_CLIENT_SECRET
    )

    if not client_id:

        raise ValueError(
            "X_CLIENT_ID is not configured."
        )

    if not client_secret:

        raise ValueError(
            "X_CLIENT_SECRET is not configured."
        )

    # --------------------------------------------------------
    # Clean accidental whitespace from copied credentials
    # --------------------------------------------------------

    client_id = client_id.strip()

    client_secret = client_secret.strip()

    # --------------------------------------------------------
    # OAuth token request body
    # --------------------------------------------------------

    data = {

        "code": code,

        "grant_type": (
            "authorization_code"
        ),

        "redirect_uri": (
            settings.X_REDIRECT_URI
        ),

        "code_verifier": (
            code_verifier
        ),
    }

    # --------------------------------------------------------
    # Build HTTP Basic Authentication manually.
    #
    # This avoids requests' default latin-1 encoding path.
    # --------------------------------------------------------

    basic_credentials = (
        f"{client_id}:{client_secret}"
    )

    basic_credentials_b64 = (
        base64.b64encode(
            basic_credentials.encode(
                "utf-8"
            )
        ).decode(
            "ascii"
        )
    )

    headers = {

        "Authorization": (
            f"Basic {basic_credentials_b64}"
        ),

        "Content-Type": (
            "application/x-www-form-urlencoded"
        ),

        "Accept": (
            "application/json"
        ),
    }

    response = requests.post(

        X_TOKEN_URL,

        data=data,

        headers=headers,

        timeout=30,
    )

    # --------------------------------------------------------
    # Helpful X error information
    # --------------------------------------------------------

    if not response.ok:

        try:

            error_data = (
                response.json()
            )

            error_message = (
                error_data.get(
                    "error_description"
                )
                or error_data.get(
                    "error"
                )
                or response.text
            )

        except ValueError:

            error_message = (
                response.text
            )

        raise ValueError(
            "X token exchange failed: "
            f"{error_message}"
        )

    return response.json()


# ============================================================
# GET X USER
# ============================================================


def get_x_user(
    access_token,
):

    headers = {

        "Authorization": (
            f"Bearer {access_token}"
        ),

        "Accept": (
            "application/json"
        ),
    }

    response = requests.get(

        X_USER_URL,

        headers=headers,

        timeout=30,
    )

    if not response.ok:

        try:

            error_data = (
                response.json()
            )

            error_message = (
                error_data.get(
                    "detail"
                )
                or error_data.get(
                    "title"
                )
                or response.text
            )

        except ValueError:

            error_message = (
                response.text
            )

        raise ValueError(
            "X user information request failed: "
            f"{error_message}"
        )

    return response.json()


# ============================================================
# SAVE X CREDENTIAL
# ============================================================


def save_x_credential(
    token_data,
    user_data,
):

    user = (
        user_data.get(
            "data",
            {}
        )
    )

    user_id = (
        user.get(
            "id"
        )
    )

    username = (
        user.get(
            "username"
        )
    )

    if not user_id:

        raise ValueError(
            "X user ID was not returned."
        )

    if not username:

        raise ValueError(
            "X username was not returned."
        )

    credential, _ = (
        XCredential.objects.update_or_create(

            user_id=user_id,

            defaults={

                "username": username,

                "token_json": (
                    json.dumps(
                        token_data
                    )
                ),

                "is_active": True,
            },
        )
    )

    return credential