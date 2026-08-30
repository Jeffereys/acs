# this file should  print all bookeo reservations for checking and validation
"""
Read-only: prints every current/future Bookeo booking (today onward). Makes
GET calls only (test_connection, get_all_bookings) -- per
Project_details.txt's Testing section, nothing here writes to Bookeo.

Bookeo's /bookings endpoint requires a startTime/endTime window capped at 31
days per call, so there's no single "give me everything" request. This walks
31-day chunks forward from today (the same method Project_details.txt's
"Full Bookeo history scope survey" used for its future-only scope), stopping
after 3 consecutive empty chunks. Past bookings are skipped entirely --
that's also consistent with push.py's own scope, since check_availability()
only ever returns future slots anyway.

Usage:
    python tests/pull_all_current_bookeo.py
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bookeo"))
from bookeo_api import BookeoAPI

CHUNK_DAYS = 31
EMPTY_CHUNK_STREAK_TO_STOP = 3


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pull_direction(api, start_cursor, step, label):
    """
    Walk CHUNK_DAYS-wide windows from start_cursor in the given direction
    (step = +timedelta or -timedelta), stopping after
    EMPTY_CHUNK_STREAK_TO_STOP consecutive empty chunks. Returns the
    collected bookings. Prints one progress line per chunk (flushed
    immediately) since a long booking history means this can be dozens of
    sequential API calls with nothing else to show for it in the meantime.
    """
    collected = []
    cursor = start_cursor
    empty_streak = 0
    chunk_num = 0

    while empty_streak < EMPTY_CHUNK_STREAK_TO_STOP:
        chunk_num += 1
        if step.total_seconds() > 0:
            window_start, window_end = cursor, cursor + step
        else:
            window_start, window_end = cursor + step, cursor

        bookings = list(api.get_all_bookings(start_time=_iso(window_start), end_time=_iso(window_end)))
        collected.extend(bookings)
        empty_streak = empty_streak + 1 if not bookings else 0
        cursor = window_end if step.total_seconds() > 0 else window_start

        print(
            f"[{label} chunk {chunk_num}] {_iso(window_start)} .. {_iso(window_end)}: "
            f"{len(bookings)} booking(s) (running total {len(collected)}, "
            f"empty streak {empty_streak}/{EMPTY_CHUNK_STREAK_TO_STOP})",
            flush=True,
        )

    return collected


def pull_all_bookings(api):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    step = timedelta(days=CHUNK_DAYS)
    return _pull_direction(api, now, step, "future")


def main():
    api = BookeoAPI()
    print("Connected to Bookeo account:", api.test_connection())

    bookings = pull_all_bookings(api)
    bookings.sort(key=lambda b: b.get("startTime") or "")

    for b in bookings:
        print(
            f"{b.get('bookingNumber')}\t{b.get('startTime')}\t"
            f"{b.get('productName')}\tcanceled={b.get('canceled')}"
        )

    print(f"\nTotal bookings pulled: {len(bookings)}")


if __name__ == "__main__":
    main()
