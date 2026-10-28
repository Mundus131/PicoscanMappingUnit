import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.services.encoder_process import EncoderProcess


class EncoderProcessTests(unittest.TestCase):
    def setUp(self):
        self.delay = 0
        self.status = 200
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_GET(self):
                time.sleep(owner.delay)
                body = json.dumps({'Velocity': 120}).encode()
                self.send_response(owner.status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.source = EncoderProcess()
        self.url = f'http://127.0.0.1:{self.server.server_port}/encoder'

    def tearDown(self):
        self.source.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def receive(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            packet = self.source.receive()
            if packet is not None:
                return packet
        self.fail('No encoder packet')

    def test_timestamp_survives_delayed_parent_consumption(self):
        self.source.configure(self.url, 'test', .05)
        first = self.receive()
        time.sleep(.35)
        second = self.receive()
        self.assertGreater(second['received'], first['received'])
        self.assertLess(second['received'], time.monotonic() - .1)
        self.assertEqual(second['payload']['data']['Velocity'], 120)
        self.assertNotIn('error', second)

    def test_auth_failure_is_explicit_and_token_change_recovers(self):
        self.status = 401
        self.source.configure(self.url, 'expired', .05)
        self.assertEqual(self.receive()['error'], 'HTTPError')
        self.status = 200
        self.source.configure(self.url, 'new', .05)
        packet = self.receive()
        self.assertEqual(packet['generation'], 2)
        self.assertIn('payload', packet)

    def test_slow_http_read_is_not_a_valid_sample(self):
        self.delay = .8
        self.source.configure(self.url, 'test', .05)
        packet = self.receive()
        self.assertIn('error', packet)
        self.assertNotIn('payload', packet)

    def test_dead_child_is_reported_and_close_is_idempotent(self):
        self.source.configure(self.url, 'test', .05)
        self.receive()
        self.source.process.terminate()
        self.source.process.join(timeout=2)
        with self.assertRaises(RuntimeError):
            self.source.receive()
        self.source.close()
        self.source.close()

    def test_source_change_discards_old_samples(self):
        self.source.configure(self.url, 'test', .05)
        self.receive()
        pid = self.source.process.pid
        self.source.configure(self.url + '/new', 'test', .05)
        self.assertNotEqual(pid, self.source.process.pid)
        self.assertEqual(self.receive()['generation'], 2)
