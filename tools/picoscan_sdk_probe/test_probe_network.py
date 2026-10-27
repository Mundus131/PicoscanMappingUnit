"""Loopback integration tests for the built Windows executable; no sensor needed."""
import pathlib
import socket
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).parent
EXE = ROOT / 'build' / 'picoscan_sdk_probe.exe'
if not EXE.exists():
    EXE = ROOT / 'build' / 'Release' / 'picoscan_sdk_probe.exe'


@unittest.skipUnless(EXE.exists(), 'Build native probe first')
class ProbeTests(unittest.TestCase):
    def test_no_packets_and_malformed_packets(self):
        for malformed in (False, True):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as directory:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
                    reservation.bind(('127.0.0.1', 0))
                    port = reservation.getsockname()[1]
                proc = subprocess.Popen(
                    [str(EXE), '127.0.0.1', '127.0.0.1', str(port), '1', str(pathlib.Path(directory)/'probe.csv')],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    if malformed:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                            for _ in range(8):
                                time.sleep(0.1)
                                sender.sendto(b'not a Compact telegram', ('127.0.0.1', port))
                    stdout, stderr = proc.communicate(timeout=10)
                    self.assertEqual(proc.returncode, 4 if malformed else 3, stdout+stderr)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.communicate()


if __name__ == '__main__':
    unittest.main()
