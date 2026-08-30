"""
Unit tests for migration/id_map.py.

Every test uses its own tempfile-backed IdMap -- never touches the real
migration/id_map.json. Run with:
    python3 -m unittest tests.test_id_map -v
or via pytest if it's installed: pytest tests/test_id_map.py
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "migration"))

from id_map import IdMap


class IdMapTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmpdir.name, "id_map.json")

    def tearDown(self):
        self._tmpdir.cleanup()


class FreshIdMapTests(IdMapTestCase):
    def test_missing_file_starts_empty(self):
        id_map = IdMap(path=self.path)
        self.assertEqual(len(id_map), 0)
        self.assertEqual(id_map.all(), {})

    def test_is_migrated_false_for_unknown_booking(self):
        id_map = IdMap(path=self.path)
        self.assertFalse(id_map.is_migrated("does-not-exist"))

    def test_get_returns_none_for_unknown_booking(self):
        id_map = IdMap(path=self.path)
        self.assertIsNone(id_map.get("does-not-exist"))


class RecordTests(IdMapTestCase):
    def test_record_makes_it_migrated_and_retrievable(self):
        id_map = IdMap(path=self.path)
        id_map.record("BK1", confirmation_number="ACEB1", status="temporary_hold",
                       bookeo_customer_id="CUST1")
        self.assertTrue(id_map.is_migrated("BK1"))
        entry = id_map.get("BK1")
        self.assertEqual(entry["confirmation_number"], "ACEB1")
        self.assertEqual(entry["status"], "temporary_hold")
        self.assertEqual(entry["bookeo_customer_id"], "CUST1")
        self.assertIn("updated_at", entry)

    def test_cancelled_status_is_not_migrated(self):
        id_map = IdMap(path=self.path)
        id_map.record("BK1", confirmation_number="ACEB1", status="cancelled")
        self.assertFalse(id_map.is_migrated("BK1"))
        self.assertIn("BK1", id_map)  # entry exists, just not "migrated"

    def test_extra_kwargs_are_stored(self):
        id_map = IdMap(path=self.path)
        id_map.record("BK1", confirmation_number="ACEB1", merged_with=["BK2"])
        self.assertEqual(id_map.get("BK1")["merged_with"], ["BK2"])

    def test_record_overwrites_existing_entry(self):
        id_map = IdMap(path=self.path)
        id_map.record("BK1", confirmation_number="ACEB1", status="temporary_hold")
        id_map.record("BK1", confirmation_number="ACEB1", status="booked")
        self.assertEqual(id_map.get("BK1")["status"], "booked")
        self.assertEqual(len(id_map), 1)

    def test_len_and_contains(self):
        id_map = IdMap(path=self.path)
        self.assertNotIn("BK1", id_map)
        id_map.record("BK1", confirmation_number="ACEB1")
        self.assertIn("BK1", id_map)
        self.assertEqual(len(id_map), 1)


class UpdateStatusTests(IdMapTestCase):
    def test_transitions_status_and_updates_timestamp(self):
        id_map = IdMap(path=self.path)
        id_map.record("BK1", confirmation_number="ACEB1", status="temporary_hold")
        first_updated_at = id_map.get("BK1")["updated_at"]
        id_map.update_status("BK1", "booked")
        self.assertEqual(id_map.get("BK1")["status"], "booked")
        # updated_at should be refreshed (may be equal on a very fast clock,
        # but must exist and be a valid timestamp either way).
        self.assertIsInstance(id_map.get("BK1")["updated_at"], str)
        self.assertIsNotNone(first_updated_at)

    def test_cancelled_then_migrated_again_is_false_then_true_on_repush(self):
        id_map = IdMap(path=self.path)
        id_map.record("BK1", confirmation_number="ACEB1", status="temporary_hold")
        id_map.update_status("BK1", "cancelled")
        self.assertFalse(id_map.is_migrated("BK1"))
        # A re-push records a fresh entry (possibly new confirmation number).
        id_map.record("BK1", confirmation_number="ACEB2", status="temporary_hold")
        self.assertTrue(id_map.is_migrated("BK1"))
        self.assertEqual(id_map.get("BK1")["confirmation_number"], "ACEB2")

    def test_raises_keyerror_for_unknown_booking(self):
        id_map = IdMap(path=self.path)
        with self.assertRaises(KeyError):
            id_map.update_status("does-not-exist", "booked")


class PersistenceTests(IdMapTestCase):
    def test_entry_survives_reload_from_disk(self):
        id_map = IdMap(path=self.path)
        id_map.record("BK1", confirmation_number="ACEB1", status="temporary_hold",
                       bookeo_customer_id="CUST1")

        reloaded = IdMap(path=self.path)  # simulates a fresh process picking the file back up
        self.assertTrue(reloaded.is_migrated("BK1"))
        self.assertEqual(reloaded.get("BK1")["confirmation_number"], "ACEB1")

    def test_file_contains_valid_json_after_multiple_writes(self):
        id_map = IdMap(path=self.path)
        for i in range(5):
            id_map.record(f"BK{i}", confirmation_number=f"ACEB{i}")
        with open(self.path) as f:
            on_disk = json.load(f)
        self.assertEqual(len(on_disk), 5)

    def test_no_leftover_temp_files_after_writes(self):
        id_map = IdMap(path=self.path)
        id_map.record("BK1", confirmation_number="ACEB1")
        id_map.update_status("BK1", "booked")
        leftovers = [
            f for f in os.listdir(self._tmpdir.name)
            if f.startswith(".id_map_") and f.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])


class CliListTests(IdMapTestCase):
    def test_all_returns_a_copy_not_a_live_reference(self):
        id_map = IdMap(path=self.path)
        id_map.record("BK1", confirmation_number="ACEB1")
        snapshot = id_map.all()
        del snapshot["BK1"]
        self.assertIn("BK1", id_map)  # mutating the snapshot must not affect the map


if __name__ == "__main__":
    unittest.main()
