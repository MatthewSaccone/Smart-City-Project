"""Tests for CSV normalization; no Docker or PostgreSQL required."""

import importlib.util
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_toronto_midblock.py"
spec = importlib.util.spec_from_file_location("import_toronto_midblock", SCRIPT)
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("America/Toronto")
        self.studies = {
            3015417: (3015417,),
        }
        self.record = {
            "id": "5226439", "count_id": "3015417",
            "time_start": "2026-02-04 11:45:00",
            "time_end": "2026-02-04 12:00:00",
            "direction": "WB",
        }
        self.record.update({col: "0" for col, _, _ in importer.SPEED_BINS})

    def test_no_traffic_means_unknown_average_speed(self):
        row = importer.observation_from_row(self.record, self.tz)
        self.assertEqual(row[6], 0)
        self.assertIsNone(row[7])
        self.assertEqual(len(row[5]), 14)

    def test_weighted_midpoint_speed(self):
        self.record["vol_20_25kph"] = "2"
        self.record["vol_36_40kph"] = "1"
        row = importer.observation_from_row(self.record, self.tz)
        self.assertEqual(row[6], 3)
        self.assertAlmostEqual(row[7], (2 * 22.5 + 38) / 3, places=3)

    def test_timestamps_convert_to_utc(self):
        value = importer.parse_source_datetime("2026-02-04 11:45:00", self.tz)
        self.assertEqual(value.isoformat(), "2026-02-04T16:45:00+00:00")

    def test_dst_ambiguous_time_rejected(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous local time"):
            importer.parse_source_datetime("2026-11-01 01:15:00", self.tz)

    def test_dst_nonexistent_time_rejected(self):
        with self.assertRaisesRegex(ValueError, "Nonexistent local time"):
            importer.parse_source_datetime("2026-03-08 02:15:00", self.tz)

    def test_reject_negative_bin_volume(self):
        self.record["vol_1_19kph"] = "-1"
        with self.assertRaisesRegex(ValueError, "Negative vehicle"):
            importer.observation_from_row(self.record, self.tz)

    def test_reject_wrong_interval(self):
        self.record["time_end"] = "2026-02-04 12:30:00"
        with self.assertRaisesRegex(ValueError, "Unexpected interval"):
            importer.observation_from_row(self.record, self.tz)


if __name__ == "__main__":
    unittest.main()
