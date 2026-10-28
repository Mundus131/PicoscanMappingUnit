"""Bounded, explicitly estimated speed hold; never refreshes its own deadline."""
from collections import deque
import math


class EncoderContinuity:
    def __init__(self):
        self.good = deque(maxlen=5)
        self.last_written = None
        self.reason = 'insufficient_samples'

    def record(self, history, stamp, speed, allow_hold=False):
        estimated = False
        self.reason = 'not_transport_failure'
        if speed is not None and math.isfinite(speed):
            if self.good and stamp - self.good[-1][0] > .5:
                self.good.clear()
            self.good.append((stamp, speed))
            self.reason = 'live_sample'
        elif allow_hold and len(self.good) == 5:
            last, value = self.good[-1]
            speeds = [v for _, v in self.good]
            stable = max(speeds) - min(speeds) <= max(.005, abs(value) * .05)
            same_direction = all(v * value >= 0 for v in speeds)
            self.reason = ('hold_expired' if not 0 <= stamp-last <= 1.0 else
                           'direction_changed' if not same_direction else
                           'unstable_speed' if not stable else 'holding')
            if 0 <= stamp - last <= 1.0 and stable and same_direction:
                speed, estimated = value, True
        elif allow_hold:
            self.reason = 'insufficient_samples'
        if estimated and self.last_written is not None:
            cursor = self.last_written + .2
            while cursor < stamp:
                history.add(cursor, speed)
                cursor += .2
        if speed is None:
            self.good.clear()
        history.add(stamp, speed)
        self.last_written = stamp
        return speed, estimated
