from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django_ratelimit.decorators import ratelimit

import os
import requests

from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import (
    urlsafe_base64_decode,
    urlsafe_base64_encode,
)


# ============================================================
# REGISTER
# ============================================================


@ratelimit(key="ip", rate="2/m", method="POST", block=True)
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
                    resend_api_key = os.environ.get(
                        "RESEND_API_KEY"
                    )

                    if not resend_api_key:
                        raise Exception(
                            "RESEND_API_KEY is not configured"
                        )

                    response = requests.post(
                        "https://api.resend.com/emails",
                        headers={
                            "Authorization": (
                                f"Bearer {resend_api_key}"
                            ),
                            "Content-Type": (
                                "application/json"
                            ),
                        },
                        json={
                            "from": os.environ.get(
                                "RESEND_FROM_EMAIL",
                                "onboarding@resend.dev",
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


@ratelimit(
    key="ip",
    rate="10/h",
    method="GET",
    block=True,
)
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
# FORGOT PASSWORD
# ============================================================



@ratelimit(
    key="ip",
    rate="2/m",
    method="POST",
    block=True,
)
def password_reset_request(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        email = request.POST.get(
            "email",
            "",
        ).strip()

        if not email:
            messages.error(
                request,
                "Please enter your email address.",
            )

            return render(
                request,
                "forgot_password.html",
            )

        # ----------------------------------------------------
        # Find user by email
        # ----------------------------------------------------

        user = User.objects.filter(
            email__iexact=email,
        ).first()

        # Temporary diagnostic.
        # Does not print the user's email.
        print(
            "PASSWORD RESET USER FOUND:",
            user is not None,
        )

        if user is not None:
            print(
                "PASSWORD RESET USER ACTIVE:",
                user.is_active,
            )

        if user is not None and user.is_active:

            # ------------------------------------------------
            # Generate secure password-reset token
            # ------------------------------------------------

            uid = urlsafe_base64_encode(
                force_bytes(user.pk)
            )

            token = default_token_generator.make_token(
                user
            )

            reset_path = reverse(
                "password_reset_confirm",
                kwargs={
                    "uidb64": uid,
                    "token": token,
                },
            )

            reset_url = request.build_absolute_uri(
                reset_path
            )

            # ------------------------------------------------
            # Password reset email
            # ------------------------------------------------

            subject = (
                "Reset your AI Video Repurposer password"
            )

            message = (
                f"Hello {user.username},\n\n"
                "We received a request to reset your "
                "AI Video Repurposer password.\n\n"
                "Open the link below to create a new password:\n\n"
                f"{reset_url}\n\n"
                "If you did not request a password reset, "
                "you can safely ignore this email.\n\n"
                "For your security, this link will expire "
                "automatically.\n\n"
                "Regards,\n"
                "AI Video Repurposer"
            )

            try:
                resend_api_key = os.environ.get(
                    "RESEND_API_KEY"
                )

                if not resend_api_key:
                    raise Exception(
                        "RESEND_API_KEY is not configured"
                    )

                resend_from_email = os.environ.get(
                    "RESEND_FROM_EMAIL",
                    "onboarding@resend.dev",
                )

                response = requests.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": (
                            f"Bearer {resend_api_key}"
                        ),
                        "Content-Type": (
                            "application/json"
                        ),
                    },
                    json={
                        "from": resend_from_email,
                        "to": [user.email],
                        "subject": subject,
                        "text": message,
                    },
                    timeout=20,
                )

                print(
                    "RESEND STATUS:",
                    response.status_code,
                )

                print(
                    "RESEND RESPONSE:",
                    response.text,
                )

                response.raise_for_status()

            except Exception as exc:
                print(
                    "PASSWORD RESET EMAIL SEND FAILED:"
                )

                print(
                    str(exc)
                )

                messages.error(
                    request,
                    "We could not send the password reset email. "
                    "Please try again later.",
                )

                return render(
                    request,
                    "forgot_password.html",
                )

        # ----------------------------------------------------
        # Same response whether email exists or not.
        # ----------------------------------------------------

        messages.success(
            request,
            "If an account with that email exists, "
            "a password reset link has been sent.",
        )

        return redirect(
            "password_reset_request"
        )

    return render(
        request,
        "forgot_password.html",
    )
def password_reset_confirm(
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
            pk=uid,
            is_active=True,
        )

    except (
        TypeError,
        ValueError,
        OverflowError,
        User.DoesNotExist,
    ):
        user = None

    # --------------------------------------------------------
    # Validate reset token
    # --------------------------------------------------------

    if (
        user is None
        or not default_token_generator.check_token(
            user,
            token,
        )
    ):
        messages.error(
            request,
            "This password reset link is invalid or has expired.",
        )

        return redirect(
            "password_reset_request"
        )

    # --------------------------------------------------------
    # Process new password
    # --------------------------------------------------------

    if request.method == "POST":
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

        if not password or not confirm_password:
            messages.error(
                request,
                "Both password fields are required.",
            )

            return render(
                request,
                "reset_password.html",
            )

        # ----------------------------------------------------
        # Password confirmation
        # ----------------------------------------------------

        if password != confirm_password:
            messages.error(
                request,
                "Passwords do not match.",
            )

            return render(
                request,
                "reset_password.html",
            )

        # ----------------------------------------------------
        # Django password validation
        # ----------------------------------------------------

        try:
            validate_password(
                password,
                user=user,
            )

        except ValidationError as exc:
            for error in exc.messages:
                messages.error(
                    request,
                    error,
                )

            return render(
                request,
                "reset_password.html",
            )

        # ----------------------------------------------------
        # Save new password
        #
        # Django's password hash change invalidates the
        # previous password-reset token.
        # ----------------------------------------------------

        user.set_password(password)

        user.save(
            update_fields=[
                "password"
            ]
        )

        messages.success(
            request,
            "Your password has been reset successfully. "
            "You can now log in.",
        )

        return redirect(
            "login"
        )

    return render(
        request,
        "reset_password.html",
    )


# ============================================================
# LOGIN
# ============================================================


@ratelimit(
    key="ip",
    rate="5/m",
    method="POST",
    block=True,
)
def login_view(request):
    if request.user.is_authenticated:
        return redirect("upload_video_page")

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

            return redirect("upload_video_page")

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
            "next": reverse("upload_video_page"),
        },
    )


# ============================================================
# LOGOUT
# ============================================================


@require_POST
def logout_view(request):
    logout(request)

    return redirect(
        "home"
    )
