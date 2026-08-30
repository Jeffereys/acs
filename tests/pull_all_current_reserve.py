# this file should  print all reserve reservations for checking and validation
"""
Read-only: verifies that every Bookeo booking this pipeline has pushed
actually landed in SCS -- i.e. that data really made it to Reserve, not just
into the local correlation table.

SCS has no fixed "list every current/future reservation" endpoint the way
Bookeo's /bookings is. Reserve/reservations.py's get_reservations() can list
reservations, but only via a Reservation Get Request that must already be
configured in SCS (Settings > Reservations > Manage Reservation Gateway Get
Requests, with whatever requestName an admin gave it) -- no such name is
recorded anywhere in this project, so it can't be called generically here.

Instead, this walks migration/id_map.json (the Bookeo bookingNumber -> SCS
confirmationNumber table migration/push.py already maintains) and, for every
non-cancelled entry, calls GetReservationServiceOptions(confirmationNumber=...)
-- the same read-only spot-check Project_details.txt used throughout to
confirm a reservation is "genuinely live in SCS and not just recorded
locally". Makes GET calls only; never writes.

Usage:
    python tests/pull_all_current_reserve.py

If you later configure a Reservation Get Request in SCS and want a true bulk
listing (matching Bookeo's account-wide pull), call
ReservationsAPI.get_reservations("<your request name>") instead -- see
reservations.py's docstring for the filters format.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "migration"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Reserve"))

from id_map import IdMap
from scs_gateway import SCSGatewayClient, SCSGatewayError
from reservations import ReservationsAPI


def _asset_names(service_options):
    """GetReservationServiceOptions's exact shape for assetNames isn't
    pinned down in reservations.py's docstring beyond "the response also
    includes assetNames for that reservation" -- check both a top-level key
    and per-result rows so this doesn't silently print nothing either way."""
    if service_options.get("assetNames"):
        return service_options["assetNames"]
    return [
        row.get("assetName")
        for row in service_options.get("results", [])
        if isinstance(row, dict) and row.get("assetName")
    ]


def verify_id_map_against_scs(reservations_api, id_map):
    entries = id_map.all()
    live_entries = {bn: e for bn, e in entries.items() if e.get("status") != "cancelled"}

    verified = []
    missing = []

    for booking_number, entry in sorted(live_entries.items(), key=lambda kv: kv[1].get("updated_at", "")):
        confirmation_number = entry["confirmation_number"]
        try:
            service_options = reservations_api.get_reservation_service_options(
                confirmation_number=confirmation_number
            )
        except SCSGatewayError as exc:
            print(f"{booking_number}\t{confirmation_number}\t[{entry['status']}]\tNOT FOUND in SCS ({exc})")
            missing.append(booking_number)
            continue

        print(
            f"{booking_number}\t{confirmation_number}\t[{entry['status']}]\t"
            f"LIVE in SCS\tassets={_asset_names(service_options)}"
        )
        verified.append(booking_number)

    return verified, missing, len(entries)


def main():
    client = SCSGatewayClient()
    print("Health check:", client.health_check())

    reservations = ReservationsAPI(client)
    id_map = IdMap()

    if not len(id_map):
        print(f"{id_map.path}: empty -- nothing has been pushed yet, nothing to verify.")
        return

    verified, missing, total_entries = verify_id_map_against_scs(reservations, id_map)

    print(
        f"\nVerified {len(verified)} live in SCS, {len(missing)} missing/errored "
        f"out of {len(verified) + len(missing)} non-cancelled id_map entries "
        f"({total_entries} total entries, including cancelled)."
    )
    if missing:
        print(f"Missing/errored booking numbers: {missing}")


if __name__ == "__main__":
    main()
