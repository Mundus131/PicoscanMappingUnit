import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.analysis import history_store as history


class HistorySummaryTests(unittest.TestCase):
    def test_latest_first_when_timestamps_share_one_millisecond(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(history, 'HISTORY_DIR', directory), \
                patch.object(history.time, 'time', return_value=1700000000), \
                patch.object(history.time, 'time_ns', side_effect=[1700000000000000001, 1700000000000000002]):
            old = history.save_measurement({'measurement_valid': False})
            new = history.save_measurement({'measurement_valid': True})
            items = history.list_measurements()
            self.assertEqual([item['id'] for item in items], [new['id'], old['id']])
            self.assertTrue(items[0]['measurement_valid'])

    def test_listing_does_not_parse_full_archives(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(history, 'HISTORY_DIR', directory):
            saved = history.save_measurement(dict(original_points=[[1, 2, 3]], metrics={'length': 10}))
            # Listing must still work even if reading the large archive would fail.
            Path(directory, saved['id']+'.json').write_text('invalid archive')
            items = history.list_measurements()
            self.assertEqual(items[0]['points_count'], 1)
            self.assertEqual(items[0]['metrics']['length'], 10)

    def test_legacy_is_lazy_and_detail_read_creates_summary(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(history, 'HISTORY_DIR', directory):
            Path(directory, 'legacy.json').write_text(json.dumps(dict(id='legacy', original_points=[[1, 2, 3]])))
            self.assertTrue(history.list_measurements()[0]['summary_pending'])
            self.assertIsNotNone(history.get_measurement('legacy'))
            self.assertEqual(history.list_measurements()[0]['points_count'], 1)

    def test_pruning_removes_summary_and_archive(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(history, 'HISTORY_DIR', directory):
            for _ in range(3):
                history.save_measurement({}, keep_last=1)
            self.assertEqual(len(list(Path(directory).glob('*.json'))), 1)
            self.assertEqual(len(list(Path(directory, '.summaries').glob('*.json'))), 1)
