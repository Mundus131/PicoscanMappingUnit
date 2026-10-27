import unittest
from app.services.trigger_guard import TriggerGuard


class TriggerGuardTests(unittest.TestCase):
    def test_startup_high_does_not_start_until_low_and_two_high_reads(self):
        g = TriggerGuard()
        self.assertEqual(g.observe(2, 0, .01), 'unarmed')
        self.assertEqual(g.observe(1, .1, .11), 'low')
        self.assertEqual(g.observe(2, .2, .21), 'pending')
        self.assertEqual(g.observe(2, .3, .31), 'high')

    def test_unknown_and_slow_reads_disarm(self):
        for state, duration in ((None, .1), (0, .1), (2, .6)):
            g = TriggerGuard()
            g.observe(1, 0, .01)
            self.assertEqual(g.observe(state, .1, .1 + duration), 'invalid')
            self.assertEqual(g.observe(2, 1, 1.01), 'unarmed')

    def test_watchdog_and_low_cancel_high_confirmation(self):
        g = TriggerGuard()
        g.observe(1, 0, .01)
        g.observe(2, .1, .11)
        g.observe(1, .2, .21)
        self.assertEqual(g.observe(2, .3, .31), 'pending')
        self.assertFalse(g.stale(.4))
        self.assertTrue(g.stale(.9))
        g.invalidate()
        self.assertEqual(g.observe(2, 1, 1.01), 'unarmed')

    def test_polling_gap_is_detected_even_when_watchdog_was_delayed(self):
        g = TriggerGuard()
        g.observe(1, 0, .01)
        self.assertEqual(g.observe(2, 1, 1.01), 'invalid')
        self.assertEqual(g.observe(2, 1.1, 1.11), 'unarmed')
