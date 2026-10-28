import threading
import os
import json
import tempfile
import multiprocessing
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch
from app.services import analysis_process as worker


def smoke_analysis(directory, snapshot):
    from app.analysis import history_store
    history_store.HISTORY_DIR = directory
    return worker._analyze(snapshot, {'active_app': 'none'})


class AnalysisProcessTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv('RUN_ANALYSIS_PROCESS_SMOKE') == '1', 'Requires full backend dependencies')
    def test_real_spawn_and_archive_preserve_invalid_quality(self):
        import numpy as np
        snapshot = dict(captured_profiles=[np.array([[1, 10, 3, 4], [2, 20, 3, 4]], dtype=np.float32)],
                        captured_profile_positions=[10], profiles_count=1, distance_mm=20,
                        profiling_distance_mm=10, quality_issues={'encoder_history_gap'},
                        measurement_encoder_error='encoder_read_failed', devices=['test'])
        snapshot['raw_frames'] = [dict(points=np.ones((3, 4)), position_mm=None)]
        snapshot['analysis_filter_settings'] = dict(frame=dict(clip_points_to_frame=True,
            width_m=.003, height_m=.01, origin_mode='center'), filters={})
        snapshot['relative_fragments'] = [dict(epoch=3, profiles_count=1,
            captured_profile_positions=[0], captured_profiles=[np.ones((2, 4))])]
        with tempfile.TemporaryDirectory() as directory:
            with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn')) as pool:
                result = pool.submit(smoke_analysis, directory, snapshot).result(timeout=60)
            self.assertFalse(result['measurement_valid'])
            self.assertIsNone(result['archive_last_error'])
            files = list(Path(directory).glob('*.json'))
            self.assertEqual(len(files), 1)
            saved = json.loads(files[0].read_text())
            self.assertEqual(len(saved['original_points']), 1)
            self.assertEqual(len(saved['raw_positioned_points']), 2)
            self.assertEqual(saved['analysis_filter_settings'], snapshot['analysis_filter_settings'])
            self.assertIsNone(saved['relative_fragments'][0]['global_offset_mm'])
            self.assertEqual(len(saved['relative_fragments'][0]['points']), 2)
            self.assertEqual(len(saved['raw_frames'][0]['points']), 3)
            self.assertIsNone(saved['raw_frames'][0]['position_mm'])
            self.assertEqual(saved['encoder_error'], 'encoder_read_failed')
            self.assertFalse(saved['measurement_valid'])

    def test_snapshot_excludes_live_locks_and_session(self):
        future = Future()
        future.set_result({'measurement_valid': True})
        pool = Mock()
        pool.submit.return_value = future
        session = dict(captured_profiles=[], quality_issues=set(),
                       lock=threading.Lock(), archive_live_session={})
        with patch.object(worker, '_pool', pool), patch.object(worker, '_slots', threading.BoundedSemaphore(2)):
            self.assertTrue(worker.analyze_capture(session, {})['measurement_valid'])
        snapshot = pool.submit.call_args.args[1]
        self.assertNotIn('lock', snapshot)
        self.assertNotIn('archive_live_session', snapshot)

    def test_full_queue_fails_without_submitting(self):
        pool = Mock()
        with patch.object(worker, '_pool', pool), patch.object(worker, '_slots', threading.BoundedSemaphore(0)):
            with self.assertRaisesRegex(RuntimeError, 'queue full'):
                worker.analyze_capture({}, {})
        pool.submit.assert_not_called()

    def test_submit_failure_releases_capacity(self):
        slots = threading.BoundedSemaphore(1)
        pool = Mock()
        pool.submit.side_effect = RuntimeError('closed')
        with patch.object(worker, '_pool', pool), patch.object(worker, '_slots', slots):
            with self.assertRaises(RuntimeError):
                worker.analyze_capture({}, {})
        self.assertTrue(slots.acquire(blocking=False))
