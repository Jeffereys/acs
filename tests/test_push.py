"""
Unit tests for migration/push.py.

Uses a FakeReservationsAPI (no network, no credentials, no dotenv/requests
dependency) so these exercise the real push_booking/push_merged_booking/
convert_to_permanent/commit_all_holds/cleanup_all_holds/find_reservation_code
logic in isolation. Never calls run_push/run_cleanup/run_commit, since those
lazily import bookeo_api/scs_gateway and hit live/real credentialed clients.

Run with:
    python3 -m unittest tests.test_push -v
or via pytest if it's installed: pytest tests/test_push.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "migration"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bookeo"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Reserve"))

from id_map import IdMap
from push import (
    PushError,
    MergeConflict,
    find_reservation_code,
    push_booking,
    push_merged_booking,
    convert_to_permanent,
    commit_all_holds,
    cleanup_all_holds,
)


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------
class FakeReservationsAPI:
    """
    Stands in for Reserve/reservations.py's ReservationsAPI. Records every
    call it receives so tests can assert on call shape/count, and lets a
    test script availability per number_of_assets key (None = default,
    unnumbered search).
    """

    def __init__(self, availability_by_assets=None, confirmation_prefix="FAKE"):
        self.availability_by_assets = (
            availability_by_assets if availability_by_assets is not None
            else {None: [{"reservationCode": "RC-DEFAULT"}]}
        )
        self.confirmation_prefix = confirmation_prefix
        self._counter = 0
        self.check_availability_calls = []
        self.book_reservation_calls = []
        self.cancel_reservation_calls = []
        self.release_temporary_hold_calls = []

    def check_availability(self, reservation_date, desired_time_from, party_size,
                            site_names=None, requests=None, max_results=100,
                            number_of_assets=None, **kwargs):
        self.check_availability_calls.append(number_of_assets)
        results = self.availability_by_assets.get(number_of_assets, [])
        return {"results": results}

    def book_reservation(self, reservation_code, first_name, last_name, email,
                          mobile_phone, temporary_hold=False, **extra):
        self._counter += 1
        confirmation_number = f"{self.confirmation_prefix}{self._counter}"
        self.book_reservation_calls.append({
            "reservation_code": reservation_code,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "mobile_phone": mobile_phone,
            "temporary_hold": temporary_hold,
            "extra": extra,
        })
        return {
            "confirmationNumber": confirmation_number,
            "uniqueId": f"uid-{confirmation_number}",
            "siteUniqueId": "site-1",
            "status": "Created",
        }

    def cancel_reservation(self, confirmation_number, cancellation_reason=None, **kwargs):
        self.cancel_reservation_calls.append((confirmation_number, cancellation_reason))
        return {"status": "OK"}

    def release_temporary_hold(self, confirmation_number):
        self.release_temporary_hold_calls.append(confirmation_number)
        return {"status": "OK"}


class RaisingRecordIdMap(IdMap):
    """An IdMap whose record() always fails, to test the
    hold-gets-cancelled-on-record-failure path."""

    def record(self, *args, **kwargs):
        raise RuntimeError("simulated id_map write failure")


def make_transformed(booking_number, email="jane@example.com", phone="555-123-4567",
                      party_size=4, **overrides):
    base = {
        "bookeo_booking_number": booking_number,
        "bookeo_customer_id": f"cust-{booking_number}",
        "reservation_date": "08/21/2026",
        "desired_time_from": "6:00 PM",
        "party_size": party_size,
        "requests": ["Bowling"],
        "first_name": "Jane",
        "last_name": "Doe",
        "email": email,
        "mobile_phone": phone,
        "comments": "",
    }
    base.update(overrides)
    return base


class PushTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.id_map_path = os.path.join(self._tmpdir.name, "id_map.json")

    def tearDown(self):
        self._tmpdir.cleanup()

    def id_map(self, cls=IdMap):
        return cls(path=self.id_map_path)


# ---------------------------------------------------------------------
# find_reservation_code
# ---------------------------------------------------------------------
class FindReservationCodeTests(PushTestCase):
    def test_default_search_succeeds_on_first_try(self):
        api = FakeReservationsAPI(availability_by_assets={None: [{"reservationCode": "RC-1"}]})
        code = find_reservation_code(api, make_transformed("BK1"))
        self.assertEqual(code, "RC-1")
        self.assertEqual(api.check_availability_calls, [None])

    def test_retries_with_increasing_number_of_assets_until_found(self):
        api = FakeReservationsAPI(availability_by_assets={
            None: [], 3: [], 4: [{"reservationCode": "RC-4"}],
        })
        code = find_reservation_code(api, make_transformed("BK1", party_size=14),
                                      max_assets_to_try=8)
        self.assertEqual(code, "RC-4")
        self.assertEqual(api.check_availability_calls, [None, 3, 4])

    def test_takes_first_result_when_multiple_returned(self):
        api = FakeReservationsAPI(availability_by_assets={
            None: [{"reservationCode": "RC-A"}, {"reservationCode": "RC-B"}],
        })
        code = find_reservation_code(api, make_transformed("BK1"))
        self.assertEqual(code, "RC-A")

    def test_raises_push_error_when_every_asset_count_is_empty(self):
        api = FakeReservationsAPI(availability_by_assets={})
        with self.assertRaises(PushError):
            find_reservation_code(api, make_transformed("BK1", party_size=20),
                                   max_assets_to_try=5)
        self.assertEqual(api.check_availability_calls, [None, 3, 4, 5])

    def test_never_tries_number_of_assets_two(self):
        # The default (unnumbered) search already auto-combines up to 2
        # assets, per Project_details.txt -- explicit 2 would be redundant.
        api = FakeReservationsAPI(availability_by_assets={})
        with self.assertRaises(PushError):
            find_reservation_code(api, make_transformed("BK1"), max_assets_to_try=4)
        self.assertNotIn(2, api.check_availability_calls)


# ---------------------------------------------------------------------
# push_booking
# ---------------------------------------------------------------------
class PushBookingTests(PushTestCase):
    def test_fresh_booking_succeeds_and_is_recorded(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        transformed = make_transformed("BK1")

        booked = push_booking(api, id_map, transformed)

        self.assertIsNotNone(booked)
        self.assertTrue(id_map.is_migrated("BK1"))
        entry = id_map.get("BK1")
        self.assertEqual(entry["status"], "temporary_hold")
        self.assertEqual(entry["confirmation_number"], booked["confirmationNumber"])
        self.assertEqual(entry["bookeo_customer_id"], "cust-BK1")
        self.assertEqual(len(api.book_reservation_calls), 1)
        self.assertTrue(api.book_reservation_calls[0]["temporary_hold"])

    def test_comments_are_passed_through_when_present(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        transformed = make_transformed("BK1", comments="Party: 6 adults")
        push_booking(api, id_map, transformed)
        self.assertEqual(api.book_reservation_calls[0]["extra"]["comments"], "Party: 6 adults")

    def test_empty_comments_are_not_sent(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        push_booking(api, id_map, make_transformed("BK1", comments=""))
        self.assertNotIn("comments", api.book_reservation_calls[0]["extra"])

    def test_idempotent_skip_when_already_migrated(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="ACEB-EXISTING", status="temporary_hold")

        result = push_booking(api, id_map, make_transformed("BK1"))

        self.assertIsNone(result)
        self.assertEqual(api.check_availability_calls, [])
        self.assertEqual(api.book_reservation_calls, [])

    def test_repush_allowed_after_cancellation(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="ACEB-OLD", status="cancelled")

        booked = push_booking(api, id_map, make_transformed("BK1"))

        self.assertIsNotNone(booked)
        self.assertEqual(len(api.book_reservation_calls), 1)

    def test_no_availability_raises_push_error_and_records_nothing(self):
        api = FakeReservationsAPI(availability_by_assets={})
        id_map = self.id_map()

        with self.assertRaises(PushError):
            push_booking(api, id_map, make_transformed("BK1"))

        self.assertFalse(id_map.is_migrated("BK1"))
        self.assertEqual(api.book_reservation_calls, [])

    def test_id_map_write_failure_cancels_the_hold_and_reraises(self):
        api = FakeReservationsAPI()
        id_map = self.id_map(cls=RaisingRecordIdMap)

        with self.assertRaises(RuntimeError):
            push_booking(api, id_map, make_transformed("BK1"))

        self.assertEqual(len(api.book_reservation_calls), 1)
        self.assertEqual(len(api.cancel_reservation_calls), 1)
        cancelled_confirmation_number, reason = api.cancel_reservation_calls[0]
        self.assertEqual(cancelled_confirmation_number, "FAKE1")
        self.assertEqual(reason, "Other")


# ---------------------------------------------------------------------
# push_merged_booking
# ---------------------------------------------------------------------
class PushMergedBookingTests(PushTestCase):
    def test_fresh_merge_books_one_reservation_for_both(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        transformed_list = [
            make_transformed("BK1", party_size=5),
            make_transformed("BK2", party_size=6),
        ]

        booked = push_merged_booking(api, id_map, transformed_list)

        self.assertIsNotNone(booked)
        self.assertEqual(len(api.book_reservation_calls), 1)  # one SCS reservation, not two
        entry1 = id_map.get("BK1")
        entry2 = id_map.get("BK2")
        self.assertEqual(entry1["confirmation_number"], entry2["confirmation_number"])
        self.assertEqual(entry1["status"], "temporary_hold")
        self.assertEqual(entry1["merged_with"], ["BK2"])
        self.assertEqual(entry2["merged_with"], ["BK1"])

    def test_party_size_and_comments_are_combined(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        transformed_list = [
            make_transformed("BK1", party_size=5, comments="Party: 5 adults"),
            make_transformed("BK2", party_size=6, comments="Party: 6 adults"),
        ]
        push_merged_booking(api, id_map, transformed_list)
        comments = api.book_reservation_calls[0]["extra"]["comments"]
        self.assertIn("Merged Bookeo bookings: BK1, BK2", comments)
        self.assertIn("Party: 5 adults", comments)
        self.assertIn("Party: 6 adults", comments)

    def test_already_fully_merged_is_a_noop(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="ACEB-MERGED", status="temporary_hold",
                       merged_with=["BK2"])
        id_map.record("BK2", confirmation_number="ACEB-MERGED", status="temporary_hold",
                       merged_with=["BK1"])

        result = push_merged_booking(api, id_map, [make_transformed("BK1"), make_transformed("BK2")])

        self.assertIsNone(result)
        self.assertEqual(api.book_reservation_calls, [])

    def test_migrated_under_different_confirmation_numbers_raises_conflict(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="ACEB-A", status="booked")
        id_map.record("BK2", confirmation_number="ACEB-B", status="booked")

        with self.assertRaises(MergeConflict):
            push_merged_booking(api, id_map, [make_transformed("BK1"), make_transformed("BK2")])

        self.assertEqual(api.book_reservation_calls, [])

    def test_partial_migration_raises_conflict(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="ACEB-A", status="booked")
        # BK2 has no entry at all.

        with self.assertRaises(MergeConflict):
            push_merged_booking(api, id_map, [make_transformed("BK1"), make_transformed("BK2")])

        self.assertEqual(api.book_reservation_calls, [])

    def test_id_map_write_failure_cancels_the_merged_hold(self):
        api = FakeReservationsAPI()
        id_map = self.id_map(cls=RaisingRecordIdMap)
        transformed_list = [make_transformed("BK1"), make_transformed("BK2")]

        with self.assertRaises(RuntimeError):
            push_merged_booking(api, id_map, transformed_list)

        self.assertEqual(len(api.book_reservation_calls), 1)
        self.assertEqual(len(api.cancel_reservation_calls), 1)
        self.assertEqual(api.cancel_reservation_calls[0][0], "FAKE1")


# ---------------------------------------------------------------------
# convert_to_permanent / commit_all_holds
# ---------------------------------------------------------------------
class ConvertToPermanentTests(PushTestCase):
    def test_converts_temporary_hold_to_booked(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="ACEB1", status="temporary_hold")

        result = convert_to_permanent(api, id_map, "BK1")

        self.assertEqual(result, "ACEB1")
        self.assertEqual(api.release_temporary_hold_calls, ["ACEB1"])
        self.assertEqual(id_map.get("BK1")["status"], "booked")

    def test_noop_when_no_entry_exists(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        result = convert_to_permanent(api, id_map, "does-not-exist")
        self.assertIsNone(result)
        self.assertEqual(api.release_temporary_hold_calls, [])

    def test_noop_when_status_is_not_temporary_hold(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="ACEB1", status="booked")
        result = convert_to_permanent(api, id_map, "BK1")
        self.assertIsNone(result)
        self.assertEqual(api.release_temporary_hold_calls, [])


class CommitAllHoldsTests(PushTestCase):
    def test_converts_only_temporary_hold_entries(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="ACEB1", status="temporary_hold")
        id_map.record("BK2", confirmation_number="ACEB2", status="temporary_hold")
        id_map.record("BK3", confirmation_number="ACEB3", status="booked")
        id_map.record("BK4", confirmation_number="ACEB4", status="cancelled")

        committed = commit_all_holds(api, id_map)

        self.assertEqual(set(committed), {"BK1", "BK2"})
        self.assertEqual(id_map.get("BK1")["status"], "booked")
        self.assertEqual(id_map.get("BK2")["status"], "booked")
        self.assertEqual(id_map.get("BK3")["status"], "booked")  # untouched, already booked
        self.assertEqual(id_map.get("BK4")["status"], "cancelled")  # untouched
        self.assertEqual(set(api.release_temporary_hold_calls), {"ACEB1", "ACEB2"})

    def test_nothing_to_commit_returns_empty_list(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="ACEB1", status="booked")
        self.assertEqual(commit_all_holds(api, id_map), [])


# ---------------------------------------------------------------------
# cleanup_all_holds
# ---------------------------------------------------------------------
class CleanupAllHoldsTests(PushTestCase):
    def test_cancels_only_temporary_hold_entries(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="ACEB1", status="temporary_hold")
        id_map.record("BK2", confirmation_number="ACEB2", status="booked")

        cancelled = cleanup_all_holds(api, id_map)

        self.assertEqual(cancelled, ["BK1"])
        self.assertEqual(id_map.get("BK1")["status"], "cancelled")
        self.assertEqual(id_map.get("BK2")["status"], "booked")  # untouched
        self.assertEqual(api.cancel_reservation_calls, [("ACEB1", "Other")])

    def test_nothing_to_clean_up_returns_empty_list(self):
        api = FakeReservationsAPI()
        id_map = self.id_map()
        self.assertEqual(cleanup_all_holds(api, id_map), [])


if __name__ == "__main__":
    unittest.main()
