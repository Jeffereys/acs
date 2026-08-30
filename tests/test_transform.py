"""
Unit tests for migration/transform.py.

Pure functions only -- no network calls, no credentials needed. Run with:
    python3 -m unittest tests.test_transform -v
or via pytest if it's installed: pytest tests/test_transform.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "migration"))

from transform import (
    split_bookeo_datetime,
    aggregate_party_size,
    format_party_breakdown,
    pick_mobile_phone,
    dedupe_customers,
    format_addons_comment,
    map_requests,
    transform_booking,
    transform_booking_to_event,
    DEFAULT_REQUEST,
    EVENT_SITE_NAME,
    EVENT_LOCATION,
    EVENT_FUNCTION_TYPE,
    EVENT_STATUS,
)


class SplitBookeoDatetimeTests(unittest.TestCase):
    def test_afternoon_time_with_negative_offset(self):
        date, time = split_bookeo_datetime("2026-07-21T12:00:00-05:00")
        self.assertEqual(date, "07/21/2026")
        self.assertEqual(time, "12:00 PM")

    def test_single_digit_hour_strips_leading_zero(self):
        date, time = split_bookeo_datetime("2026-08-21T06:00:00-05:00")
        self.assertEqual(date, "08/21/2026")
        self.assertEqual(time, "6:00 AM")

    def test_midnight_stays_twelve_am(self):
        _, time = split_bookeo_datetime("2026-01-01T00:00:00-06:00")
        self.assertEqual(time, "12:00 AM")

    def test_noon_stays_twelve_pm(self):
        _, time = split_bookeo_datetime("2026-01-01T12:00:00-06:00")
        self.assertEqual(time, "12:00 PM")

    def test_positive_offset_no_conversion_applied(self):
        # Bookeo's offset is venue-local already -- transform must NOT shift it.
        date, time = split_bookeo_datetime("2026-07-21T09:30:00+02:00")
        self.assertEqual(date, "07/21/2026")
        self.assertEqual(time, "9:30 AM")


class AggregatePartySizeTests(unittest.TestCase):
    def test_sums_multiple_categories(self):
        participants = {"numbers": [
            {"peopleCategoryId": "Cadults", "number": 2},
            {"peopleCategoryId": "Cchildren", "number": 3},
        ]}
        self.assertEqual(aggregate_party_size(participants), 5)

    def test_single_category(self):
        self.assertEqual(
            aggregate_party_size({"numbers": [{"peopleCategoryId": "Cadults", "number": 6}]}), 6
        )

    def test_none_participants(self):
        self.assertEqual(aggregate_party_size(None), 0)

    def test_empty_numbers_list(self):
        self.assertEqual(aggregate_party_size({"numbers": []}), 0)

    def test_missing_number_key_defaults_to_zero(self):
        self.assertEqual(aggregate_party_size({"numbers": [{"peopleCategoryId": "Cadults"}]}), 0)


class FormatPartyBreakdownTests(unittest.TestCase):
    def test_multiple_categories_joined(self):
        participants = {"numbers": [
            {"peopleCategoryId": "Cadults", "number": 2},
            {"peopleCategoryId": "Cchildren", "number": 3},
        ]}
        self.assertEqual(format_party_breakdown(participants), "2 Cadults, 3 Cchildren")

    def test_empty_returns_empty_string(self):
        self.assertEqual(format_party_breakdown({"numbers": []}), "")
        self.assertEqual(format_party_breakdown(None), "")


class PickMobilePhoneTests(unittest.TestCase):
    def test_prefers_mobile_over_others(self):
        phones = [
            {"type": "home", "number": "111"},
            {"type": "mobile", "number": "222"},
            {"type": "work", "number": "333"},
        ]
        self.assertEqual(pick_mobile_phone(phones), "222")

    def test_falls_back_to_cell_when_no_mobile(self):
        phones = [{"type": "home", "number": "111"}, {"type": "cell", "number": "222"}]
        self.assertEqual(pick_mobile_phone(phones), "222")

    def test_falls_back_to_first_entry_when_no_priority_type_matches(self):
        phones = [{"type": "fax", "number": "999"}]
        self.assertEqual(pick_mobile_phone(phones), "999")

    def test_empty_list_returns_none(self):
        self.assertIsNone(pick_mobile_phone([]))

    def test_none_returns_none(self):
        self.assertIsNone(pick_mobile_phone(None))


class DedupeCustomersTests(unittest.TestCase):
    def test_distinct_email_phone_pairs_are_kept_separate(self):
        customers = [
            {"id": "1", "emailAddress": "a@x.com", "phoneNumbers": [{"type": "mobile", "number": "111"}]},
            {"id": "2", "emailAddress": "b@x.com", "phoneNumbers": [{"type": "mobile", "number": "222"}]},
        ]
        result = dedupe_customers(customers)
        self.assertEqual({c["id"] for c in result}, {"1", "2"})

    def test_matching_email_and_phone_collapsed_to_one(self):
        customers = [
            {"id": "dup-1", "emailAddress": "same@x.com",
             "phoneNumbers": [{"type": "mobile", "number": "555"}], "numBookings": 1},
            {"id": "dup-2", "emailAddress": "same@x.com",
             "phoneNumbers": [{"type": "mobile", "number": "555"}], "numBookings": 1},
        ]
        result = dedupe_customers(customers)
        self.assertEqual(len(result), 1)

    def test_email_match_is_case_insensitive(self):
        customers = [
            {"id": "1", "emailAddress": "Same@X.com",
             "phoneNumbers": [{"type": "mobile", "number": "555"}]},
            {"id": "2", "emailAddress": "same@x.com",
             "phoneNumbers": [{"type": "mobile", "number": "555"}]},
        ]
        self.assertEqual(len(dedupe_customers(customers)), 1)

    def test_higher_numbookings_wins_on_duplicate(self):
        customers = [
            {"id": "low", "emailAddress": "a@x.com",
             "phoneNumbers": [{"type": "mobile", "number": "111"}], "numBookings": 1},
            {"id": "high", "emailAddress": "a@x.com",
             "phoneNumbers": [{"type": "mobile", "number": "111"}], "numBookings": 9},
        ]
        result = dedupe_customers(customers)
        self.assertEqual(result[0]["id"], "high")

    def test_tiebreak_on_equal_numbookings_picks_latest_creationtime(self):
        # NOTE: this pins down ACTUAL behavior, not the documented intent.
        # dedupe_customers()'s docstring claims "ties keep whichever has the
        # earliest creationTime", but the implementation uses max() on the
        # creationTime string, which selects the LATEST (lexicographically
        # greatest, i.e. most recent) ISO8601 timestamp on a tie -- the
        # opposite of what's documented. See the accompanying validation
        # note; this test documents current behavior so a future fix (either
        # the code or the docstring) has to consciously update it.
        customers = [
            {"id": "earlier", "emailAddress": "a@x.com",
             "phoneNumbers": [{"type": "mobile", "number": "111"}],
             "numBookings": 1, "creationTime": "2024-01-01T00:00:00Z"},
            {"id": "later", "emailAddress": "a@x.com",
             "phoneNumbers": [{"type": "mobile", "number": "111"}],
             "numBookings": 1, "creationTime": "2025-06-01T00:00:00Z"},
        ]
        result = dedupe_customers(customers)
        self.assertEqual(result[0]["id"], "later")


class FormatAddonsCommentTests(unittest.TestCase):
    def test_multiple_options_joined_by_newline(self):
        options = [{"name": "Shoe size", "value": "10"}, {"name": "Pizza", "value": "Pepperoni"}]
        self.assertEqual(format_addons_comment(options), "Shoe size: 10\nPizza: Pepperoni")

    def test_options_with_falsy_value_are_skipped(self):
        options = [{"name": "Shoe size", "value": ""}, {"name": "Pizza", "value": "Pepperoni"}]
        self.assertEqual(format_addons_comment(options), "Pizza: Pepperoni")

    def test_empty_or_none_returns_empty_string(self):
        self.assertEqual(format_addons_comment([]), "")
        self.assertEqual(format_addons_comment(None), "")


class MapRequestsTests(unittest.TestCase):
    def test_bowling_substring_match_is_case_insensitive(self):
        self.assertEqual(map_requests("Weekend BOWLING Reservations"), ["Bowling"])

    def test_unmatched_product_falls_back_to_default(self):
        self.assertEqual(map_requests("Laser Tag Party"), DEFAULT_REQUEST)

    def test_none_product_name_falls_back_to_default(self):
        self.assertEqual(map_requests(None), DEFAULT_REQUEST)

    def test_passes_validation_when_value_is_available(self):
        self.assertEqual(map_requests("Bowling Lane", available_requests=["Bowling"]), ["Bowling"])

    def test_raises_when_mapped_value_not_in_available_requests(self):
        with self.assertRaises(ValueError):
            map_requests("Bowling Lane", available_requests=["Something Else"])

    def test_no_validation_performed_when_available_requests_omitted(self):
        # Should not raise even though nothing is "configured" -- validation
        # only kicks in when a fresh options pull is supplied.
        self.assertEqual(map_requests("Bowling Lane"), ["Bowling"])


class TransformBookingTests(unittest.TestCase):
    def _booking(self, **overrides):
        base = {
            "bookingNumber": "BK1",
            "customerId": "CUST1",
            "productName": "Weekend Bowling Reservations - 90 min",
            "startTime": "2026-08-21T18:00:00-05:00",
            "participants": {"numbers": [{"peopleCategoryId": "Cadults", "number": 6}]},
            "options": [{"name": "Shoe size", "value": "10"}],
        }
        base.update(overrides)
        return base

    def _customer(self, **overrides):
        base = {
            "id": "CUST1",
            "firstName": "Jane",
            "lastName": "Doe",
            "emailAddress": "jane@example.com",
            "phoneNumbers": [{"type": "mobile", "number": "555-123-4567"}],
        }
        base.update(overrides)
        return base

    def test_happy_path_full_transform(self):
        result = transform_booking(self._booking(), self._customer())
        self.assertEqual(result["bookeo_booking_number"], "BK1")
        self.assertEqual(result["bookeo_customer_id"], "CUST1")
        self.assertEqual(result["reservation_date"], "08/21/2026")
        self.assertEqual(result["desired_time_from"], "6:00 PM")
        self.assertEqual(result["party_size"], 6)
        self.assertEqual(result["requests"], ["Bowling"])
        self.assertEqual(result["first_name"], "Jane")
        self.assertEqual(result["last_name"], "Doe")
        self.assertEqual(result["email"], "jane@example.com")
        self.assertEqual(result["mobile_phone"], "555-123-4567")
        self.assertIn("Party: 6 Cadults", result["comments"])
        self.assertIn("Shoe size: 10", result["comments"])

    def test_raises_when_customer_has_no_phone(self):
        with self.assertRaises(ValueError):
            transform_booking(self._booking(), self._customer(phoneNumbers=[]))

    def test_validates_requests_against_available_requests(self):
        with self.assertRaises(ValueError):
            transform_booking(
                self._booking(productName="Bowling Special"),
                self._customer(),
                available_requests=["SomethingElse"],
            )

    def test_no_breakdown_or_addons_still_returns_empty_comments(self):
        booking = self._booking(participants={"numbers": []}, options=[])
        result = transform_booking(booking, self._customer())
        self.assertEqual(result["comments"], "")


class TransformBookingToEventTests(unittest.TestCase):
    def _booking(self, **overrides):
        base = {
            "bookingNumber": "BK1",
            "customerId": "CUST1",
            "productName": "Weekend Bowling Reservations - 90 min",
            "startTime": "2026-08-29T18:00:00-05:00",
            "endTime": "2026-08-29T19:30:00-05:00",
            "participants": {"numbers": [{"peopleCategoryId": "Cadults", "number": 4}]},
            "options": [{"name": "Shoe size", "value": "10"}],
        }
        base.update(overrides)
        return base

    def _customer(self, **overrides):
        base = {
            "id": "CUST1",
            "firstName": "Jane",
            "lastName": "Doe",
            "emailAddress": "jane@example.com",
            "phoneNumbers": [{"type": "mobile", "number": "555-123-4567"}],
        }
        base.update(overrides)
        return base

    def test_happy_path_full_transform(self):
        result = transform_booking_to_event(self._booking(), self._customer())

        self.assertEqual(result["bookeo_booking_number"], "BK1")
        self.assertEqual(result["bookeo_customer_id"], "CUST1")

        fields = result["fields"]
        self.assertEqual(fields["function.event.site"], EVENT_SITE_NAME)
        self.assertEqual(fields["function.event.name"], "Jane Doe")
        self.assertEqual(fields["function.event.lifecycleState.stateType"], EVENT_STATUS)
        self.assertEqual(fields["function.event.estimatedAttendance"], "4")
        self.assertEqual(fields["function.estimatedAttendance"], "4")
        self.assertEqual(fields["function.event.contact.firstName"], "Jane")
        self.assertEqual(fields["function.event.contact.lastName"], "Doe")
        self.assertEqual(fields["function.event.contact.email"], "jane@example.com")
        self.assertEqual(fields["function.event.contact.mobilePhone"], "555-123-4567")
        self.assertEqual(fields["function.startDate"], "08/29/2026")
        self.assertEqual(fields["function.startTime"], "6:00 PM")
        self.assertEqual(fields["function.endTime"], "7:30 PM")
        self.assertEqual(fields["function.functionType"], EVENT_FUNCTION_TYPE)
        self.assertEqual(fields["function.locations"], EVENT_LOCATION)
        self.assertIn("Party: 4 Cadults", fields["function.event.notes"])
        self.assertIn("Shoe size: 10", fields["function.event.notes"])

    def test_no_notes_field_when_nothing_to_say(self):
        booking = self._booking(participants={"numbers": []}, options=[])
        result = transform_booking_to_event(booking, self._customer())
        self.assertNotIn("function.event.notes", result["fields"])

    def test_raises_when_customer_has_no_last_name(self):
        with self.assertRaises(ValueError):
            transform_booking_to_event(self._booking(), self._customer(lastName=""))

    def test_raises_when_customer_has_no_phone(self):
        with self.assertRaises(ValueError):
            transform_booking_to_event(self._booking(), self._customer(phoneNumbers=[]))


if __name__ == "__main__":
    unittest.main()
