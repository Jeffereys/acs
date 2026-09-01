"""
Unit tests for migration/push_events.py.

Uses a FakeEventsAPI (no network, no credentials) so these exercise the
real push_event_booking() logic in isolation. Never calls run_push_events(),
since that lazily imports bookeo_api/scs_gateway/events and hits live
credentialed clients.

Run with:
    python3 -m unittest tests.test_push_events -v
or via pytest if it's installed: pytest tests/test_push_events.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "migration"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bookeo"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Reserve"))

from id_map import IdMap
from push_events import EventPushError, push_event_booking


class FakeEventsAPI:
    """Stands in for Reserve/events.py's EventsAPI. Records every
    create_event() call and returns a scripted EventFunctionImport-shaped
    response."""

    def __init__(self, status="Created", messages=None):
        self.status = status
        self.messages = messages
        self._counter = 0
        self.create_event_calls = []

    def create_event(self, fields, mode="test"):
        self._counter += 1
        self.create_event_calls.append({"fields": dict(fields), "mode": mode})
        if mode == "test":
            return {"results": [{"rowNumber": 0, "status": self.status, "messages": self.messages, "uniqueIds": None}]}
        return {
            "results": [{
                "rowNumber": 0,
                "status": self.status,
                "messages": self.messages,
                "uniqueIds": {
                    "event.uniqueId": f"event-{self._counter}",
                    "uniqueId": f"function-{self._counter}",
                },
            }]
        }


def make_transformed(booking_number, **field_overrides):
    fields = {
        "function.event.interfaceAccountId": f"BKO:{booking_number}",
        "function.event.site": "Alley Cats Entertainment, Burleson",
        "function.event.name": "Jane Doe",
        "function.event.lifecycleState.stateType": "New",
        "function.event.estimatedAttendance": "4",
        "function.event.contact.firstName": "Jane",
        "function.event.contact.lastName": "Doe",
        "function.event.contact.email": "jane@example.com",
        "function.event.contact.mobilePhone": "555-123-4567",
        "function.startDate": "08/29/2026",
        "function.startTime": "6:00 PM",
        "function.endTime": "7:30 PM",
        "function.functionType": "Miscellaneous",
        "function.locations": "Bowling Lanes",
        "function.estimatedAttendance": "4",
    }
    fields.update(field_overrides)
    return {
        "bookeo_booking_number": booking_number,
        "bookeo_customer_id": f"cust-{booking_number}",
        "fields": fields,
    }


class PushEventsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.id_map_path = os.path.join(self._tmpdir.name, "event_id_map.json")

    def tearDown(self):
        self._tmpdir.cleanup()

    def id_map(self):
        return IdMap(path=self.id_map_path)


class PushEventBookingTests(PushEventsTestCase):
    def test_mode_test_validates_and_records_nothing(self):
        api = FakeEventsAPI(status="Created")
        id_map = self.id_map()
        transformed = make_transformed("BK1")

        result = push_event_booking(api, id_map, transformed, mode="test")

        self.assertEqual(result["status"], "Created")
        self.assertFalse(id_map.is_migrated("BK1"))
        self.assertEqual(len(api.create_event_calls), 1)
        self.assertEqual(api.create_event_calls[0]["mode"], "test")

    def test_mode_apply_creates_and_records(self):
        api = FakeEventsAPI(status="Created")
        id_map = self.id_map()
        transformed = make_transformed("BK1")

        result = push_event_booking(api, id_map, transformed, mode="apply")

        self.assertEqual(result["status"], "Created")
        self.assertTrue(id_map.is_migrated("BK1"))
        entry = id_map.get("BK1")
        self.assertEqual(entry["status"], "created")
        self.assertEqual(entry["confirmation_number"], "function-1")
        self.assertEqual(entry["site_unique_id"], "event-1")
        self.assertEqual(entry["bookeo_customer_id"], "cust-BK1")

    def test_merged_status_is_recorded_as_migrated(self):
        api = FakeEventsAPI(status="Merged")
        id_map = self.id_map()
        push_event_booking(api, id_map, make_transformed("BK1"), mode="apply")
        self.assertEqual(id_map.get("BK1")["status"], "merged")
        self.assertTrue(id_map.is_migrated("BK1"))

    def test_idempotent_skip_when_already_migrated(self):
        api = FakeEventsAPI()
        id_map = self.id_map()
        id_map.record("BK1", confirmation_number="function-existing", status="created")

        result = push_event_booking(api, id_map, make_transformed("BK1"), mode="apply")

        self.assertIsNone(result)
        self.assertEqual(api.create_event_calls, [])

    def test_failed_status_raises_and_records_nothing(self):
        api = FakeEventsAPI(status="Failed", messages=["Event Site is required."])
        id_map = self.id_map()

        with self.assertRaises(EventPushError):
            push_event_booking(api, id_map, make_transformed("BK1"), mode="apply")

        self.assertFalse(id_map.is_migrated("BK1"))

    def test_repush_allowed_after_no_prior_success(self):
        # A booking that previously failed (never recorded) can be retried.
        api = FakeEventsAPI(status="Created")
        id_map = self.id_map()
        result = push_event_booking(api, id_map, make_transformed("BK1"), mode="apply")
        self.assertIsNotNone(result)
        self.assertTrue(id_map.is_migrated("BK1"))

    def test_unparseable_response_raises_and_records_nothing(self):
        class BadAPI:
            def create_event(self, fields, mode="test"):
                return {"unexpected": "shape"}

        id_map = self.id_map()
        with self.assertRaises(EventPushError):
            push_event_booking(BadAPI(), id_map, make_transformed("BK1"), mode="apply")
        self.assertFalse(id_map.is_migrated("BK1"))


if __name__ == "__main__":
    unittest.main()
