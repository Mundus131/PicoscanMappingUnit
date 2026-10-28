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

    def test_last_known_signed_speed_is_used_even_after_acceleration(self):
        for values in ((1, 1, 1, 1, -1), (1, 1.2, 1, 1, 1)):
            c, h = self.stable(values)
            self.assertEqual(c.record(h, 1.5, None, True), (values[-1], True))

    def test_real_stop_is_used_immediately(self):
        c, h = self.stable()
        self.assertEqual(c.record(h, 1.0, 0), (0, False))

    def test_non_transport_failure_cannot_be_held(self):
        c, h = self.stable()
        self.assertEqual(c.record(h, 1.0, None), (None, False))
        self.assertEqual(c.reason, 'not_transport_failure')

    def test_reason_explains_insufficient_history(self):
        c, h = EncoderContinuity(), MotionHistory()
        c.record(h, .3, None, True)
        self.assertEqual(c.reason, 'insufficient_samples')

    def test_single_read_is_enough_and_recovery_does_not_reposition_outage(self):
        c, h = EncoderContinuity(), MotionHistory()
        c.record(h, 0, .5)
        c.record(h, .4, None, True)
        self.assertEqual(c.record(h, .8, 1), (1, False))
        self.assertTrue(c.gap_estimated)
        self.assertAlmostEqual(h.distance(0, .8)[0], 400)
        c.record(h, 1, 1)
        self.assertAlmostEqual(h.distance(0, 1)[0], 600)

    def test_recovery_after_limit_does_not_hide_gap(self):
        c, h = self.stable()
        c.record(h, 1.5, None, True)
        c.record(h, 2, 1)
        self.assertIsNone(h.distance(0, 2)[0])

    def test_unlimited_hold_and_recovery(self):
        c, h = EncoderContinuity(max_hold_s=0), MotionHistory()
        c.record(h, 0, .5)
        self.assertEqual(c.record(h, 10, None, True), (.5, True))
        c.record(h, 10.2, 0)
        self.assertAlmostEqual(h.distance(0, 10.2)[0], 5100)

    def test_custom_hold_limit(self):
        c, h = EncoderContinuity(max_hold_s=3), MotionHistory()
        c.record(h, 0, -.5)
        self.assertEqual(c.record(h, 2, None, True), (-.5, True))
        self.assertEqual(c.record(h, 3.1, None, True), (None, False))
