from urllib.parse import urlencode
import json

import requests

from django.conf import settings

from .models import InstagramCredential


# ============================================================
# INSTAGRAM OAUTH PERMISSIONS
# ============================================================

INSTAGRAM_SCOPES = [
    'instagram_business_basic',
    'instagram_business_content_publish',
     'instagram_business_manage_insights',
]


# ============================================================
# INSTAGRAM AUTHORIZATION URL
# ============================================================

def get_instagram_authorization_url():
    """
    Create the Instagram Business Login authorization URL.
    """

    params = {
        'client_id': settings.INSTAGRAM_APP_ID,
        'redirect_uri': settings.INSTAGRAM_REDIRECT_URI,
        'response_type': 'code',
        'scope': ','.join(INSTAGRAM_SCOPES),
        'force_reauth': 'true',
    }

    authorization_url = (
        'https://www.instagram.com/oauth/authorize?'
        + urlencode(params)
    )

    return authorization_url


# ============================================================
# EXCHANGE AUTHORIZATION CODE FOR SHORT-LIVED TOKEN
# ============================================================

def exchange_instagram_code_for_token(code):
    """
    Exchange Instagram authorization code
    for a short-lived access token.
    """

    response = requests.post(
        'https://api.instagram.com/oauth/access_token',
        data={
            'client_id': settings.INSTAGRAM_APP_ID,
            'client_secret': settings.INSTAGRAM_APP_SECRET,
            'grant_type': 'authorization_code',
            'redirect_uri': settings.INSTAGRAM_REDIRECT_URI,
            'code': code,
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# EXCHANGE SHORT-LIVED TOKEN FOR LONG-LIVED TOKEN
# ============================================================

def exchange_short_lived_token_for_long_lived_token(
    short_lived_token
):
    """
    Exchange the short-lived Instagram access token
    for a long-lived access token.
    """

    response = requests.get(
        'https://graph.instagram.com/access_token',
        params={
            'grant_type': 'ig_exchange_token',
            'client_secret': settings.INSTAGRAM_APP_SECRET,
            'access_token': short_lived_token,
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# GET INSTAGRAM ACCOUNT INFORMATION
# ============================================================

def get_instagram_account_info(access_token):
    """
    Get the Instagram account information
    for the authenticated Instagram user.
    """

    response = requests.get(
        'https://graph.instagram.com/me',
        params={
            'fields': 'user_id,username',
            'access_token': access_token,
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get('user_id'):
        raise ValueError(
            'Instagram user ID was not returned.'
        )

    if not data.get('username'):
        raise ValueError(
            'Instagram username was not returned.'
        )

    return {
        'account_id': data['user_id'],
        'account_name': data['username'],
    }


# ============================================================
# SAVE INSTAGRAM CREDENTIALS
# ============================================================

def save_instagram_credentials(
    access_token,
    token_data,
    account_info
):
    """
    Save or update Instagram OAuth credentials.

    If the same Instagram account is connected again,
    update the existing database record.

    If a different Instagram account is connected,
    create a new Instagram database record.
    """

    stored_token_data = {
        'access_token': access_token,
        'token_type': token_data.get(
            'token_type',
            'bearer'
        ),
        'expires_in': token_data.get(
            'expires_in'
        ),
        'account_id': account_info['account_id'],
        'account_name': account_info['account_name'],
    }

    token_json = json.dumps(
        stored_token_data
    )

    instagram_credential, created = (
        InstagramCredential.objects.update_or_create(
            account_id=account_info['account_id'],
            defaults={
                'account_name': account_info['account_name'],
                'token_json': token_json,
                'is_active': True,
            },
        )
    )

    return instagram_credential