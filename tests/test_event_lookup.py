"""
Unit tests for migration/event_lookup.py.

Uses a FakeGatewayClient (no network) that serves canned, paged
BookeoMigration_Lookup responses. Run with:
    python3 -m unittest tests.test_event_lookup -v
"""

import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "migration"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bookeo"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Reserve"))

from event_lookup import (
    LOOKUP_REQUEST_NAME,
    iter_events,
    migrated_booking_numbers,
    migrated_event_index,
    rebuild_id_map,
    to_mmddyyyy,
)
from id_map import IdMap

HEADER = [
    "interfaceAccountId", "uniqueId", "eventNumber", "name",
    "startDate", "startTime", "lifecycleState.stateType",
    "functions.locations.name",
]


def row(marker, uid, evno, name="Guest Name", date="11/15/2027",
        time="2:00 PM", status="Option Hold 5", loc="Bowling Lanes"):
    return [marker, uid, evno, name, date, time, status, loc]


class FakeGatewayClient:
    """Serves `all_rows` in pages of 100, honoring first_result/max_results.
    Records every get_request call."""

    def __init__(self, all_rows):
        self.all_rows = all_rows
        self.calls = []

    def get_request(self, request_name, filters=None, max_results=100, first_result=0, **kw):
        self.calls.append({"request_name": request_name, "filters": filters,
                           "max_results": max_results, "first_result": first_result})
        first = first_result or 0
        page = self.all_rows[first:first + max_results]
        return {
            "rootEntity": "event",
            "count": len(self.all_rows),
            "header": HEADER,
            "results": page,
        }


class ToMmddyyyyTests(unittest.TestCase):
    def test_passthrough_mmddyyyy(self):
        self.assertEqual(to_mmddyyyy("08/21/2026"), "08/21/2026")

    def test_iso_with_z(self):
        self.assertEqual(to_mmddyyyy("2026-08-21T00:00:00Z"), "08/21/2026")

    def test_iso_with_offset(self):
        self.assertEqual(to_mmddyyyy("2026-08-21T18:00:00-05:00"), "08/21/2026")

    def test_date_object(self):
        self.assertEqual(to_mmddyyyy(datetime.date(2026, 8, 21)), "08/21/2026")

    def test_plain_iso_date(self):
        self.assertEqual(to_mmddyyyy("2026-08-21"), "08/21/2026")


class IterEventsTests(unittest.TestCase):
    def test_only_yields_bookeo_markers(self):
        client = FakeGatewayClient([
            row("BKO:111", "u1", "1-1"),
            row(None, "u2", "2-1"),                      # staff-created, no marker
            row("SomethingElse:9", "u3", "3-1"),         # other integration
            row("BKO:222", "u4", "4-1"),
        ])
        got = list(iter_events(client, "11/01/2027", "11/30/2027"))
        self.assertEqual([r["interfaceAccountId"] for r in got], ["BKO:111", "BKO:222"])

    def test_pages_through_all_results(self):
        rows = [row(f"BKO:{i}", f"u{i}", f"{i}-1") for i in range(250)]
        client = FakeGatewayClient(rows)
        got = list(iter_events(client, "11/01/2027", "11/30/2027", widen_days=0))
        self.assertEqual(len(got), 250)
        # 3 pages: 100 + 100 + 50
        self.assertEqual([c["first_result"] for c in client.calls], [0, 100, 200])

    def test_window_is_widened_by_one_day_each_side(self):
        client = FakeGatewayClient([])
        list(iter_events(client, "11/10/2027", "11/20/2027", widen_days=1))
        f = client.calls[0]["filters"]
        self.assertEqual(f[0], ["startDate", "GREATER_THAN_OR_EQUAL_TO", "11/09/2027"])
        self.assertEqual(f[1], ["startDate", "LESS_THAN_OR_EQUAL_TO", "11/21/2027"])

    def test_accepts_iso_window_bounds(self):
        client = FakeGatewayClient([])
        list(iter_events(client, "2027-11-10T00:00:00Z", "2027-11-20T00:00:00Z", widen_days=0))
        self.assertEqual(client.calls[0]["request_name"], LOOKUP_REQUEST_NAME)
        f = client.calls[0]["filters"]
        self.assertEqual(f[0][2], "11/10/2027")
        self.assertEqual(f[1][2], "11/20/2027")


class ConvenienceShapeTests(unittest.TestCase):
    def _client(self):
        return FakeGatewayClient([
            row("BKO:1577600000000001", "evt-uid-1", "15801-1", name="Alice A",
                date="11/15/2027", status="Option Hold 5"),
            row("BKO:1577600000000002", "evt-uid-2", "15802-1", name="Bob B",
                date="11/16/2027", status="New"),
            row(None, "staff-uid", "15803-1"),
        ])

    def test_migrated_booking_numbers(self):
        nums = migrated_booking_numbers(self._client(), "11/01/2027", "11/30/2027")
        self.assertEqual(nums, {"1577600000000001", "1577600000000002"})

    def test_migrated_event_index(self):
        idx = migrated_event_index(self._client(), "11/01/2027", "11/30/2027")
        self.assertEqual(idx["1577600000000001"]["event_unique_id"], "evt-uid-1")
        self.assertEqual(idx["1577600000000001"]["event_number"], "15801-1")
        self.assertEqual(idx["1577600000000001"]["status"], "Option Hold 5")
        self.assertEqual(idx["1577600000000002"]["name"], "Bob B")

    def test_rebuild_id_map_from_scs(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "event_id_map.json")
            id_map = rebuild_id_map(self._client(), "11/01/2027", "11/30/2027", path=path)

            self.assertEqual(len(id_map), 2)
            self.assertTrue(id_map.is_migrated("1577600000000001"))
            entry = id_map.get("1577600000000001")
            self.assertEqual(entry["site_unique_id"], "evt-uid-1")
            self.assertEqual(entry["confirmation_number"], "15801-1")
            self.assertEqual(entry["status"], "option hold 5")
            self.assertEqual(entry["source"], "rebuilt_from_scs")

            # survives reload
            self.assertTrue(IdMap(path=path).is_migrated("1577600000000002"))


if __name__ == "__main__":
    unittest.main()
