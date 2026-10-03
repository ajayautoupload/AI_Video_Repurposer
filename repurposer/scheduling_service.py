from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone


# ============================================================
# SCHEDULE TIMEZONE
# ============================================================

SCHEDULE_TIMEZONE = ZoneInfo(
    "Asia/Kolkata"
)


# ============================================================
# DAILY SCHEDULE SLOTS
# ============================================================

DAILY_SCHEDULE_SLOTS = [
    (12, 0),    # Reel 1 → 12:00 PM IST
    (19, 30),   # Reel 2 → 7:30 PM IST
]


# ============================================================
# MINIMUM SCHEDULE LEAD TIME
# ============================================================

# Keep a safety buffer between the current time and the selected
# scheduling slot. This is important for platforms such as Facebook
# where the video may take time to upload before the final scheduling
# request is sent.

MIN_SCHEDULE_LEAD_MINUTES = 60


# ============================================================
# GET NEXT SCHEDULE SLOTS
# ============================================================

def get_next_schedule_slots(number_of_slots):
    """
    Return the next safe scheduling slots.

    All slots are calculated in Indian Standard Time (IST)
    and returned as timezone-aware UTC datetimes.

    A slot is considered available only when it is at least
    MIN_SCHEDULE_LEAD_MINUTES in the future. This prevents a long
    video upload from reaching the platform after the selected
    slot has already passed.
    """

    if number_of_slots <= 0:
        return []

    now_utc = timezone.now()

    minimum_allowed_utc = (
        now_utc
        + timedelta(
            minutes=MIN_SCHEDULE_LEAD_MINUTES
        )
    )

    minimum_allowed_ist = (
        minimum_allowed_utc.astimezone(
            SCHEDULE_TIMEZONE
        )
    )

    current_date = minimum_allowed_ist.date()

    slots = []

    while len(slots) < number_of_slots:

        for hour, minute in DAILY_SCHEDULE_SLOTS:

            slot_datetime_ist = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                hour,
                minute,
                tzinfo=SCHEDULE_TIMEZONE,
            )

            if slot_datetime_ist <= minimum_allowed_ist:
                continue

            slot_datetime_utc = (
                slot_datetime_ist.astimezone(
                    dt_timezone.utc
                )
            )

            slots.append(
                slot_datetime_utc
            )

            if len(slots) >= number_of_slots:
                break

        current_date += timedelta(
            days=1
        )

    return slots