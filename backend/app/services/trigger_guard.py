"""Fail-closed trigger qualification, independent of transport implementation."""
class TriggerGuard:
    def __init__(self, max_age=0.5):
        self.max_age = max_age
        self.last_good = None
        self.armed = False
        self.high_count = 0

    def invalidate(self):
        self.last_good = None
        self.armed = False
        self.high_count = 0

    def stale(self, now):
        return self.last_good is None or now - self.last_good > self.max_age

    def observe(self, state, started, now):
        if (state not in (1, 2) or now - started > self.max_age
                or (self.last_good is not None and now - self.last_good > self.max_age)):
            self.invalidate()
            return 'invalid'
        self.last_good = now
        if state == 1:
            self.armed = True
            self.high_count = 0
            return 'low'
        if not self.armed:
            return 'unarmed'
        self.high_count += 1
        return 'high' if self.high_count >= 2 else 'pending'
