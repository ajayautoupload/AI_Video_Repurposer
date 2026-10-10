from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django_ratelimit.decorators import ratelimit
import os
import requests
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import (
    url_has_allowed_host_and_scheme,
    urlsafe_base64_decode,
    urlsafe_base64_encode,
)


# ============================================================
# REGISTER
# ============================================================

@ratelimit(key="ip", rate="5/h", method="POST", block=True)
def register_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        username = request.POST.get(
            "username",
            "",
        ).strip()

        email = request.POST.get(
            "email",
            "",
        ).strip()

        password = request.POST.get(
            "password",
            "",
        )

        confirm_password = request.POST.get(
            "confirm_password",
            "",
        )

        # ----------------------------------------------------
        # Required fields
        # ----------------------------------------------------

        if not username or not email or not password:
            messages.error(
                request,
                "Username, email and password are required.",
            )

        # ----------------------------------------------------
        # Password confirmation
        # ----------------------------------------------------

        elif password != confirm_password:
            messages.error(
                request,
                "Passwords do not match.",
            )

        # ----------------------------------------------------
        # Username check
        # ----------------------------------------------------

        elif User.objects.filter(
            username__iexact=username
        ).exists():
            messages.error(
                request,
                "Username already exists.",
            )

        # ----------------------------------------------------
        # Email check
        # ----------------------------------------------------

        elif User.objects.filter(
            email__iexact=email
        ).exists():
            messages.error(
                request,
                "Email already exists.",
            )

        else:
            # ------------------------------------------------
            # Password validation
            # ------------------------------------------------

            try:
                validate_password(password)

            except ValidationError as exc:
                for error in exc.messages:
                    messages.error(
                        request,
                        error,
                    )

            else:
                # --------------------------------------------
                # Create user as inactive.
                #
                # The user cannot log in until the email
                # verification link is opened.
                # --------------------------------------------

                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    is_active=False,
                )

                # --------------------------------------------
                # Generate secure verification token
                # --------------------------------------------

                uid = urlsafe_base64_encode(
                    force_bytes(user.pk)
                )

                token = default_token_generator.make_token(
                    user
                )

                verification_path = reverse(
                    "verify_email",
                    kwargs={
                        "uidb64": uid,
                        "token": token,
                    },
                )

                verification_url = request.build_absolute_uri(
                    verification_path
                )

                # --------------------------------------------
                # Verification email
                # --------------------------------------------

                subject = (
                    "Verify your AI Video Repurposer account"
                )

                message = (
                    f"Hello {user.username},\n\n"
                    "Thank you for registering with "
                    "AI Video Repurposer.\n\n"
                    "Please verify your email address by "
                    "opening this link:\n\n"
                    f"{verification_url}\n\n"
                    "After verification, you can log in and "
                    "use the application.\n\n"
                    "If you did not create this account, "
                    "you can ignore this email.\n\n"
                    "Regards,\n"
                    "AI Video Repurposer"
                )

                try:
                    resend_api_key = os.environ.get("RESEND_API_KEY")

                    if not resend_api_key:
                        raise Exception("RESEND_API_KEY is not configured")

                    response = requests.post(
                        "https://api.resend.com/emails",
                        headers={
                            "Authorization": f"Bearer {resend_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "from": os.environ.get(
                                "RESEND_FROM_EMAIL",
                                "onboarding@resend.dev"
                            ),
                            "to": [user.email],
                            "subject": subject,
                            "text": message,
                        },
                        timeout=20,
                    )

                    response.raise_for_status()

                except Exception as exc:
                    # ----------------------------------------
                    # If email cannot be sent, remove the
                    # inactive account so a broken account
                    # is not left in the database.
                    # ----------------------------------------

                    user.delete()

                    print(
                        "EMAIL VERIFICATION SEND FAILED:"
                    )

                    print(
                        str(exc)
                    )

                    messages.error(
                        request,
                        "Verification email could not be sent. "
                        "Please check the email service configuration and try again.",
                    )

                else:
                    messages.success(
                        request,
                        "Registration successful. "
                        "Check your email and click the "
                        "verification link before logging in.",
                    )

                    return redirect("login")

    return render(
        request,
        "register.html",
    )


# ============================================================
# VERIFY EMAIL
# ============================================================

@ratelimit(key="ip", rate="10/h", method="GET", block=True)
def verify_email(
    request,
    uidb64,
    token,
):
    # --------------------------------------------------------
    # Decode user ID
    # --------------------------------------------------------

    try:
        uid = urlsafe_base64_decode(
            uidb64
        ).decode()

        user = User.objects.get(
            pk=uid
        )

    except (
        TypeError,
        ValueError,
        OverflowError,
        User.DoesNotExist,
    ):
        user = None

    # --------------------------------------------------------
    # Validate verification token
    # --------------------------------------------------------

    if (
        user is not None
        and default_token_generator.check_token(
            user,
            token,
        )
    ):
        # --------------------------------------------
        # Activate only after successful verification
        # --------------------------------------------

        if not user.is_active:
            user.is_active = True

            user.save(
                update_fields=[
                    "is_active"
                ]
            )

        messages.success(
            request,
            "Email verified successfully. "
            "You can now log in.",
        )

        return redirect("login")

    # --------------------------------------------------------
    # Invalid or expired link
    # --------------------------------------------------------

    messages.error(
        request,
        "This verification link is invalid or has expired.",
    )

    return redirect("login")


# ============================================================
# LOGIN
# ============================================================

@ratelimit(key="ip", rate="5/m", method="POST" , block=True)
def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    next_url = (
        request.GET.get("next")
        or request.POST.get("next")
        or "/"
    )

    if not url_has_allowed_host_and_scheme(
        next_url,
        {request.get_host()},
    ):
        next_url = "/"

    if request.method == "POST":
        username = request.POST.get(
            "username",
            "",
        ).strip()

        password = request.POST.get(
            "password",
            "",
        )

        user = authenticate(
            request,
            username=username,
            password=password,
        )

        if user is not None:
            login(
                request,
                user,
            )

            return redirect(
                next_url
            )

        # ----------------------------------------------------
        # Detect an existing but unverified account.
        # Django's normal authentication rejects inactive
        # users, so check the credentials separately only
        # to show the correct message.
        # ----------------------------------------------------

        existing_user = User.objects.filter(
            username__iexact=username
        ).first()

        if (
            existing_user is not None
            and existing_user.check_password(password)
            and not existing_user.is_active
        ):
            messages.error(
                request,
                "Please verify your email before logging in.",
            )

        else:
            messages.error(
                request,
                "Invalid username or password.",
            )

    return render(
        request,
        "login.html",
        {
            "next": next_url,
        },
    )


# ============================================================
# LOGOUT
# ============================================================


def logout_view(request):
    logout(request)

    return redirect(
        "home"
    )
