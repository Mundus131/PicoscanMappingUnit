import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.services.capture_pipeline import MotionHistory, FrameSynchronizer


class MotionTests(unittest.TestCase):
    def test_interpolates_speed_and_integrates_at_frame_time(self):
        h = MotionHistory()
        h.add(0, 0)
        h.add(.2, .4)
        distance, error = h.distance(0, .1)
        self.assertIsNone(error)
        self.assertAlmostEqual(distance, 10)
        self.assertAlmostEqual(h.distance(.1, .2)[0], 30)

    def test_never_extrapolates(self):
        h = MotionHistory()
        h.add(0, .2)
        self.assertEqual(h.distance(0, .1), (None, 'pending'))

    def test_invalid_sample_breaks_entire_capture_distance(self):
        h = MotionHistory()
        for t, speed in ((0, .2), (.1, None), (.2, .2), (.3, .2)):
            h.add(t, speed)
        self.assertEqual(h.distance(0, .3)[1], 'encoder_history_gap')
        self.assertEqual(h.distance(.1, .2)[1], 'encoder_invalid')

    def test_long_gap_and_nan_are_invalid(self):
        for speed in (-.2, float('nan'), float('inf'), .2):
            h = MotionHistory()
            h.add(0, .2)
            h.add(1, speed)
            self.assertIsNone(h.distance(0, .5)[0])

    def test_reverse_reduces_position(self):
        h = MotionHistory()
        for t, v in ((0, 1), (.2, 1), (.4, -1), (.6, -1)):
            h.add(t, v)
        self.assertAlmostEqual(h.distance(0, .2)[0], 200)
        self.assertAlmostEqual(h.distance(0, .6)[0], 0)
        self.assertIsNone(h.distance(0, .6)[1])

    def test_history_bound(self):
        h = MotionHistory(capacity=2)
        for t in (0, .1, .2):
            h.add(t, .2)
        self.assertEqual(h.distance(0, .2)[1], 'encoder_history_missing')


def frame(t, sequence=1, complete=True):
    return dict(received_monotonic=t, frame=sequence, complete=complete)


class PairingTests(unittest.TestCase):
    def test_every_group_requires_both_devices(self):
        sync = FrameSynchronizer(['a', 'b'])
        issues = set()
        sync.add('a', [frame(.1)], 0, issues)
        self.assertEqual(sync.ready(.11, issues), [])
        sync.add('b', [frame(.11)], 0, issues)
        groups = sync.ready(.12, issues)
        self.assertEqual(set(groups[0]), {'a', 'b'})
        self.assertFalse(issues)

    def test_no_mixing_distant_scans(self):
        sync = FrameSynchronizer(['a', 'b'])
        issues = set()
        sync.add('a', [frame(.1)], 0, issues)
        sync.add('b', [frame(.2)], 0, issues)
        self.assertEqual(sync.ready(.2, issues), [])
        self.assertIn('scanner_unsynchronized', issues)

    def test_partial_duplicate_old_and_overflow(self):
        sync = FrameSynchronizer(['a'], capacity=1)
        issues = set()
        sync.add('a', [frame(-1), frame(.1, complete=False), frame(.2, 2), frame(.2, 2), frame(.3, 3)], 0, issues)
        self.assertEqual(len(sync.ready(.3, issues)), 1)
        self.assertIn('incomplete_frame', issues)
        self.assertIn('frame_queue_overflow', issues)

    def test_missing_scanner_times_out(self):
        sync = FrameSynchronizer(['a', 'b'])
        issues = set()
        sync.add('a', [frame(.1)], 0, issues)
        self.assertEqual(sync.ready(1, issues), [])
        self.assertIn('scanner_missing', issues)


if __name__ == '__main__':
    unittest.main()
