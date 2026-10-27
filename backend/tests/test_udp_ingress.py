"""Real loopback socket / spawned-process ingress tests (no scanner required)."""
import socket
import time
import unittest

from app.services.udp_ingress import ProcessUDPHandler


class IngressTests(unittest.TestCase):
    def test_recovers_from_old_backlog_and_counts_discarded_packets(self):
        transport = ProcessUDPHandler('127.0.0.1', 0)
        try:
            self.send(transport.port, 20)
            self.wait_received(transport, 20)
            time.sleep(1.1)
            self.send(transport.port, 1)
            self.wait_received(transport, 21)
            time.sleep(0.05)
            transport.receive_new_scan_segment()
            metrics = transport.get_metrics()
            self.assertEqual(metrics['ingress_stale_dropped'], 20)
            self.assertEqual(metrics['ingress_queue_dropped'], 20)
            self.assertEqual(metrics['ingress_queue_depth'], 0)
            self.assertLess(metrics['ingress_queue_age_s'], 1)
        finally:
            transport.close()

    def test_sustained_150_datagrams_per_second_with_consumer_stalls(self):
        import threading
        transport = ProcessUDPHandler("127.0.0.1", 0)
        count = 1500
        def produce():
            start = time.monotonic()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                for i in range(count):
                    time.sleep(max(0, start + i / 150 - time.monotonic()))
                    sender.sendto(i.to_bytes(4, 'little') + b'x' * 12000,
                                  ('127.0.0.1', transport.port))
        producer = threading.Thread(target=produce)
        producer.start()
        try:
            for i in range(count):
                if i % 150 == 0:
                    time.sleep(0.4)
                payload, _ = transport.receive_new_scan_segment()
                self.assertEqual(int.from_bytes(payload[:4], 'little'), i)
            producer.join(timeout=2)
            self.assertEqual(transport.get_metrics()['ingress_queue_dropped'], 0)
        finally:
            producer.join(timeout=12)
            transport.close()

    def send(self, port, count):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for i in range(count):
                sender.sendto(i.to_bytes(4, "little"), ("127.0.0.1", port))
                time.sleep(0.001)

    def wait_received(self, transport, count):
        deadline = time.monotonic() + 3
        while transport.get_metrics()["datagrams_received"] < count and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(transport.get_metrics()["datagrams_received"], count)

    def test_slow_consumer_preserves_order_and_original_receipt_time(self):
        transport = ProcessUDPHandler("127.0.0.1", 0)
        try:
            self.send(transport.port, 150)
            self.wait_received(transport, 150)
            time.sleep(0.2)
            before_drain = time.monotonic()
            stamps = []
            for i in range(150):
                payload, _ = transport.receive_new_scan_segment()
                self.assertEqual(int.from_bytes(payload, "little"), i)
                stamps.append(transport.last_host_receipt[1])
            self.assertEqual(stamps, sorted(stamps))
            self.assertLess(stamps[-1], before_drain - 0.1)
            self.assertEqual(transport.get_metrics()["ingress_queue_dropped"], 0)
            self.assertEqual(transport.get_metrics()["ingress_queue_depth"], 0)
        finally:
            transport.close()
        self.assertFalse(transport._process.is_alive())

    def test_overload_is_bounded_counted_and_shutdown_does_not_hang(self):
        transport = ProcessUDPHandler("127.0.0.1", 0, queue_capacity=4)
        self.send(transport.port, 40)
        self.wait_received(transport, 40)
        metrics = transport.get_metrics()
        self.assertEqual(metrics["ingress_queue_depth"], 4)
        self.assertEqual(metrics["ingress_queue_dropped"], 36)
        transport.close()
        transport.close()
        self.assertFalse(transport._process.is_alive())

    def test_bind_error_is_reported_and_port_can_be_reused_after_close(self):
        transport = ProcessUDPHandler("127.0.0.1", 0)
        port = transport.port
        try:
            with self.assertRaises(OSError):
                ProcessUDPHandler("127.0.0.1", port)
        finally:
            transport.close()
        replacement = ProcessUDPHandler("127.0.0.1", port)
        replacement.close()


if __name__ == "__main__":
    unittest.main()
