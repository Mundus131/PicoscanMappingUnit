import unittest
from unittest.mock import patch
import numpy as np
from app.services.raw_capture import retain, archive_fields


class RawCaptureTests(unittest.TestCase):
    def test_frames_survive_encoder_failure_without_invented_position(self):
        session = dict(capture_started_monotonic=10, quality_issues={'encoder_history_gap'})
        points = np.ones((2, 4), dtype=np.float32)
        retain(session, 'scanner', [dict(points=points, received_monotonic=11, frame=3)])
        points[:] = 0
        archived = archive_fields(session)
        self.assertEqual(archived['raw_frames'][0]['points'][0], [1]*4)
        self.assertIsNone(archived['raw_frames'][0]['position_mm'])
        self.assertEqual(archived['raw_frames'][0]['received_offset_s'], 1)

    def test_memory_limit_is_explicit_and_capture_boundaries_are_respected(self):
        session = dict(capture_started_monotonic=10, capture_stopped_monotonic=12)
        frames = [dict(points=np.ones((2, 4)), received_monotonic=t) for t in (9, 11, 13)]
        with patch('app.services.raw_capture.MAX_RAW_BYTES', 1):
            retain(session, 'a', frames)
        self.assertEqual(session['raw_frames_received'], 1)
        self.assertEqual(session['raw_frames_dropped'], 1)
        self.assertIn('raw_capture_overflow', session['quality_issues'])
