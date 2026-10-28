"""Bounded, explicitly estimated speed hold; never refreshes its own deadline."""
from collections import deque
import math


class EncoderContinuity:
    def __init__(self, max_hold_s=1.0):
        self.max_hold_s = max_hold_s
        self.good = deque(maxlen=5)
        self.last_written = None
        self.reason = 'insufficient_samples'
        self.holding = False
        self.gap_estimated = False

    def record(self, history, stamp, speed, allow_hold=False):
        estimated = False
        self.gap_estimated = False
        self.reason = 'not_transport_failure'
        if speed is not None and math.isfinite(speed):
            if self.good and (self.holding or stamp - self.good[-1][0] > .5):
                last, value = self.good[-1]
                if self._within_limit(stamp - last):
                    # End the outage at the held speed. The new reading applies only
                    # from its receipt onward, without retroactively accelerating.
                    self._bridge(history, math.nextafter(stamp, -math.inf), value)
                    self.gap_estimated = True
                else:
                    history.add(math.nextafter(stamp, -math.inf), None)
            self.good.append((stamp, speed))
            self.reason = 'live_sample'
        elif allow_hold and self.good:
            last, value = self.good[-1]
            self.reason = 'holding' if self._within_limit(stamp - last) else 'hold_expired'
            if self._within_limit(stamp - last):
                speed, estimated = value, True
        elif allow_hold:
            self.reason = 'insufficient_samples'
        if estimated and self.last_written is not None:
            self._bridge(history, stamp, speed)
        if speed is None:
            self.good.clear()
        history.add(stamp, speed)
        self.last_written = stamp
        self.holding = estimated
        return speed, estimated

    def _bridge(self, history, stamp, speed):
        cursor = self.last_written + .2
        while cursor < stamp:
            history.add(cursor, speed)
            cursor += .2
        history.add(stamp, speed)

    def _within_limit(self, elapsed):
        return elapsed >= 0 and (self.max_hold_s == 0 or elapsed <= self.max_hold_s)
