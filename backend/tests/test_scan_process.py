"""End-to-end synthetic Compact stream through the production process boundary."""
import os
import socket
import struct
import threading
import time
import unittest
import zlib
from unittest.mock import patch
from app.services.scan_process import ScanProcessReceiver


def telegram(frame, segment):
    beams = 600
    meta = struct.pack('<QQIIII', segment, frame, 1, 1, beams, 3)
    meta += struct.pack('<QQffffIBBBB', frame, frame, 0., -.5, .5, 1., 0, 0, 3, 2, 0)
    beam = struct.pack('<HHHHHHH', 1000, 10, 0, 0, 0, 0, 16384)
    module = meta + beam * beams
    payload = struct.pack('<4sIQQII', b'\x02'*4, 1, frame*10+segment, frame, 4, len(module)) + module
    return payload + struct.pack('<I', zlib.crc32(payload))


class ScanProcessTests(unittest.TestCase):
    def test_complete_frames_at_15hz_with_stalled_parent(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        config = dict(receiver_type='picoscan', device_id='test', device_type='picoscan',
                      format_type='compact', listen_ip='127.0.0.1', listen_port=port,
                      segments_per_scan=10, segment_pattern=list(range(10)),
                      fallback_enabled=False, fallback_listen_ip=None,
                      fallback_listen_port=None, fallback_formats=[])
        receiver = ScanProcessReceiver(config, {})
        def send():
            start = time.monotonic()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                for frame in range(60):
                    time.sleep(max(0, start + frame/15 - time.monotonic()))
                    for segment in range(10):
                        sock.sendto(telegram(frame, segment), ('127.0.0.1', port))
        with patch.dict(os.environ, {'DEBUG': 'false'}):
            self.assertTrue(receiver.start_listening(), receiver.last_error)
        sender = threading.Thread(target=send)
        sender.start()
        frames = []
        try:
            time.sleep(.4)
            deadline = time.monotonic() + 12
            while len(frames) < 60 and time.monotonic() < deadline:
                packet = receiver.receive_packet()
                if packet:
                    frames.extend(packet['frames'])
            self.assertEqual([f['device_frame'] for f in frames], list(range(60)))
            self.assertTrue(all(f['points'].shape == (6000, 4) for f in frames))
            self.assertEqual(receiver.get_metrics()['processing_queue_dropped'], 0)
            self.assertEqual(receiver.get_metrics()['ingress']['ingress_queue_dropped'], 0)
        finally:
            sender.join(timeout=5)
            receiver.stop_listening()
        self.assertFalse(receiver.connected)
