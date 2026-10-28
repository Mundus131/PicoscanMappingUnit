import threading
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch
from app.services import archive_process as archive


class ArchiveQueueTests(unittest.TestCase):
    def test_returns_before_write_and_publishes_completion(self):
        future = Future()
        pool = Mock()
        pool.submit.return_value = future
        live = {'capture_started_ts': 1}
        session = dict(capture_started_ts=1, archive_live_session=live, analysis_metrics={'length': 10})
        with patch.object(archive, '_pool', pool), patch.object(archive, '_slots', threading.BoundedSemaphore(2)):
            archive.queue_archive(session, {})
            self.assertTrue(live['archive_pending'])
            self.assertFalse(future.done())
            self.assertEqual(session['analysis_metrics']['length'], 10)
            future.set_result(dict(archive_pending=False, archive_last_error=None))
            self.assertFalse(live['archive_pending'])

    def test_old_archive_cannot_update_new_capture(self):
        future = Future()
        pool = Mock()
        pool.submit.return_value = future
        live = {'capture_started_ts': 1}
        session = dict(capture_started_ts=1, archive_live_session=live)
        with patch.object(archive, '_pool', pool), patch.object(archive, '_slots', threading.BoundedSemaphore(2)):
            archive.queue_archive(session, {})
            live.update(capture_started_ts=2, archive_pending=True)
            future.set_result(dict(archive_pending=False, archive_last_error=None))
            self.assertTrue(live['archive_pending'])
            self.assertFalse(session['archive_pending'])

    def test_full_queue_reports_archive_error_without_touching_metrics(self):
        session = dict(analysis_metrics={'length': 10}, measurement_valid=True)
        with patch.object(archive, '_slots', threading.BoundedSemaphore(0)):
            archive.queue_archive(session, {})
        self.assertFalse(session['archive_pending'])
        self.assertIn('queue full', session['archive_last_error'])
        self.assertTrue(session['measurement_valid'])
        self.assertEqual(session['analysis_metrics']['length'], 10)

    def test_write_failure_releases_slot(self):
        future = Future()
        pool = Mock()
        pool.submit.return_value = future
        slots = threading.BoundedSemaphore(1)
        session = {}
        with patch.object(archive, '_pool', pool), patch.object(archive, '_slots', slots):
            archive.queue_archive(session, {})
            future.set_exception(OSError('disk full'))
        self.assertFalse(session['archive_pending'])
        self.assertIn('OSError', session['archive_last_error'])
        self.assertTrue(slots.acquire(blocking=False))
