import logging
import types
import unittest
from app.services.scan_logging import SegmentProgressFilter, quiet_segment_progress


class ScanLoggingTests(unittest.TestCase):
    def test_only_progress_is_suppressed(self):
        f = SegmentProgressFilter()
        for level, message, expected in (
            (logging.INFO, 'Received segment 0.', False),
            (logging.DEBUG, 'Received segment 12.', False),
            (logging.WARNING, 'Received segment 0.', True),
            (logging.ERROR, 'CRC failed', True),
            (logging.INFO, 'Listening on UDP', True),
            (logging.INFO, 'Received segment x.', True),
        ):
            record = logging.LogRecord('scansegmentapi', level, '', 1, message, (), None)
            self.assertEqual(bool(f.filter(record)), expected)

    def test_installed_on_library_logger_once_and_keeps_warnings(self):
        logger = logging.Logger('isolated')
        module = types.SimpleNamespace(logger=logger)
        quiet_segment_progress(module)
        quiet_segment_progress(module)
        self.assertEqual(len(logger.filters), 1)
        quiet_segment_progress(types.SimpleNamespace())
