"""Bounded host-time frame pairing and encoder interpolation, without hardware IO."""
from collections import deque
import math
import threading


class MotionHistory:
    def __init__(self, capacity=4096, max_gap=0.5):
        self.samples = deque(maxlen=capacity)
        self.max_gap = max_gap
        self.lock = threading.Lock()

    def add(self, timestamp, speed):
        if speed is not None and not math.isfinite(speed):
            speed = None
        with self.lock:
            if self.samples and timestamp <= self.samples[-1][0]:
                return
            position, epoch = (self.samples[-1][2:]) if self.samples else (0.0, 0)
            if self.samples:
                t, v, _, _ = self.samples[-1]
                if v is None or speed is None or timestamp - t > self.max_gap:
                    epoch += 1
                else:
                    position += (v + speed) * 0.5 * (timestamp - t) * 1000.0
            self.samples.append((timestamp, speed, position, epoch))

    def position(self, timestamp):
        with self.lock:
            samples = list(self.samples)
        if not samples or timestamp > samples[-1][0]:
            return None, "pending"
        if timestamp < samples[0][0]:
            return None, "encoder_history_missing"
        for i, (t, speed, position, epoch) in enumerate(samples):
            if t == timestamp:
                return ((position, epoch), None) if speed is not None else (None, "encoder_invalid")
            if t > timestamp:
                prev_t, prev_speed, prev_position, prev_epoch = samples[i - 1]
                if speed is None or prev_speed is None or epoch != prev_epoch:
                    return None, "encoder_history_gap"
                dt = timestamp - prev_t
                acceleration = (speed - prev_speed) / (t - prev_t)
                return (prev_position + (prev_speed * dt + 0.5 * acceleration * dt * dt) * 1000, epoch), None
        return None, "encoder_history_missing"

    def distance(self, start, end):
        origin, error = self.position(start)
        if error:
            return None, error
        target, error = self.position(end)
        if error:
            return None, error
        if origin[1] != target[1]:
            return None, "encoder_history_gap"
        return target[0] - origin[0], None


class FrameSynchronizer:
    """Pair complete frames by host receipt time; never pair by unrelated scan IDs."""
    def __init__(self, devices, tolerance=0.02, timeout=0.5, capacity=256):
        self.queues = {d: deque() for d in devices}
        self.tolerance = tolerance
        self.timeout = timeout
        self.capacity = capacity
        self.last_frame = {}

    def add(self, device, frames, start, issues):
        queue = self.queues[device]
        for frame in frames:
            stamp = frame.get("received_monotonic")
            if stamp is None or not math.isfinite(stamp):
                issues.add("frame_timestamp_missing")
                continue
            if stamp < start:
                continue
            if not frame.get("complete", False):
                issues.add("incomplete_frame")
                continue
            sequence = frame["frame"]
            previous = self.last_frame.get(device)
            if previous is not None and sequence <= previous:
                continue
            if previous is not None and sequence > previous + 1:
                issues.add("frames_skipped")
            self.last_frame[device] = sequence
            if len(queue) >= self.capacity:
                queue.popleft()
                issues.add("frame_queue_overflow")
            queue.append(frame)

    def ready(self, now, issues):
        groups = []
        while self.queues and all(self.queues.values()):
            times = {d: q[0]["received_monotonic"] for d, q in self.queues.items()}
            oldest = min(times, key=times.get)
            if max(times.values()) - times[oldest] <= self.tolerance:
                groups.append({d: q.popleft() for d, q in self.queues.items()})
            else:
                self.queues[oldest].popleft()
                issues.add("scanner_unsynchronized")
        for queue in self.queues.values():
            while queue and now - queue[0]["received_monotonic"] > self.timeout:
                queue.popleft()
                issues.add("scanner_missing")
        return groups

    def unfinished(self):
        return any(self.queues.values())
