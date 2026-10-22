"""Finalizer contracts without loading hardware clients."""
import ast
from collections import deque
import logging
from pathlib import Path
import threading
import types
import unittest


class FinalizationTests(unittest.TestCase):
    def run_stop(self, fail=False, replace=False):
        session = dict(recording=True, capture_started_ts=1, captured_profiles=[],
                       captured_profile_positions=[], quality_issues=set(), profiles_count=2,
                       pending_profile_groups=deque(),
                       frame_synchronizer=types.SimpleNamespace(queues={}, tolerance=.02),
                       captured_preview_blob_b64='OLD', captured_preview_cache_dirty=False)
        app = types.SimpleNamespace(state=types.SimpleNamespace(receiver_manager=None))
        ns = dict(time=types.SimpleNamespace(monotonic=lambda: 2), deque=deque,
                  _get_session_from_app=lambda app: session, _sync_encoder_worker=lambda s: None,
                  _update_session_once=lambda *args: None, _session_lock=threading.RLock(),
                  logger=logging.getLogger(__name__))

        def consume(s, final=False):
            if fail:
                raise ValueError('bad cloud')

        def analyze(s, app):
            s.update(captured_preview_points_np='NEW_POINTS', captured_preview_cache_dirty=False,
                     captured_preview_blob_b64='NEW_BLOB', measurement_valid=not s['quality_issues'])
            if replace:
                session.update(capture_started_ts=3, analysis_pending=True)

        ns.update(_consume_positioned_groups=consume, _run_analysis_for_session=analyze,
                  _position_for_frame=lambda *a: (100., None),
                  threading=types.SimpleNamespace(Thread=lambda target, **kw:
                      types.SimpleNamespace(start=target)))
        path = Path(__file__).parents[1] / 'app/api/endpoints/acquisition.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_stop_trigger_session_locked')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
        ns['_stop_trigger_session_locked'](app)
        return session

    def test_finalized_preview_does_not_reuse_stale_blob(self):
        session = self.run_stop()
        self.assertEqual(session['captured_preview_points_np'], 'NEW_POINTS')
        self.assertEqual(session['captured_preview_blob_b64'], '')
        self.assertTrue(session['captured_preview_cache_dirty'])
        self.assertFalse(session['analysis_pending'])

    def test_exception_marks_capture_and_clears_pending(self):
        with self.assertLogs(level='ERROR'):
            session = self.run_stop(fail=True)
        self.assertFalse(session['analysis_pending'])
        self.assertFalse(session['measurement_valid'])
        self.assertIn('capture_finalization_failed', session['quality_issues'])

    def test_old_finalizer_does_not_overwrite_new_capture(self):
        session = self.run_stop(replace=True)
        self.assertEqual(session['capture_started_ts'], 3)
        self.assertTrue(session['analysis_pending'])
        self.assertEqual(session['captured_preview_blob_b64'], 'OLD')
