import unittest
from app.services.encoder_continuity import EncoderContinuity
from app.services.capture_pipeline import MotionHistory


class EncoderContinuityTests(unittest.TestCase):
    def stable(self, values=(1, 1, 1, 1, 1)):
        c, h = EncoderContinuity(), MotionHistory()
        for i, value in enumerate(values):
            c.record(h, i*.2, value)
        return c, h

    def test_short_timeout_is_explicitly_estimated_and_bridges_gap(self):
        c, h = self.stable()
        self.assertEqual(c.record(h, 1.5, None, True), (1, True))
        self.assertAlmostEqual(h.distance(0, 1.5)[0], 1500)

    def test_hold_deadline_does_not_slide(self):
        c, h = self.stable()
        c.record(h, 1.5, None, True)
        self.assertEqual(c.record(h, 1.9, None, True), (None, False))
        self.assertEqual(c.reason, 'hold_expired')
        self.assertIsNone(h.distance(0, 1.9)[0])

    def test_unstable_direction_or_speed_cannot_be_held(self):
        for values in ((1, 1, 1, 1, -1), (1, 1.2, 1, 1, 1)):
            c, h = self.stable(values)
            self.assertEqual(c.record(h, 1.5, None, True), (None, False))

    def test_real_stop_is_used_immediately(self):
        c, h = self.stable()
        self.assertEqual(c.record(h, 1.0, 0), (0, False))

    def test_non_transport_failure_cannot_be_held(self):
        c, h = self.stable()
        self.assertEqual(c.record(h, 1.0, None), (None, False))
        self.assertEqual(c.reason, 'not_transport_failure')

    def test_reason_explains_insufficient_history(self):
        c, h = EncoderContinuity(), MotionHistory()
        c.record(h, 0, 1)
        c.record(h, .3, None, True)
        self.assertEqual(c.reason, 'insufficient_samples')
