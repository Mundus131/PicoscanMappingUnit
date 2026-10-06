"""Exercise acquisition math without starting hardware clients on import."""
import ast
import logging
from pathlib import Path
import threading
import types
import unittest

import numpy as np


class CaptureRegressionTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.frame = 1
        self.motion = {"mode": "fixed", "fixed_speed_mps": 0.2, "profiling_distance_mm": 5}
        self.session = dict(recording=True, last_motion_monotonic=0., distance_mm=0.,
                            last_profile_distance_mm=0., devices=["scanner"], profiles=[])
        cloud = np.array([[1., 0., 2.], [2., 0., 3.]])
        self.receiver = types.SimpleNamespace(get_latest_point_cloud=lambda _: {
            "frame": self.frame, "timestamp": self.clock, "points": cloud})
        manager = types.SimpleNamespace(motion_settings=self.motion,
                                       get_device=lambda _: types.SimpleNamespace(calibration={}))
        self.ns = dict(np=np, logger=logging.getLogger(__name__),
                       time=types.SimpleNamespace(time=lambda: self.clock,
                           monotonic=lambda: self.clock, perf_counter=lambda: self.clock),
                       device_manager=manager, _session_lock=threading.RLock(),
                       _sync_encoder_worker=lambda _: None, _record_session_stage=lambda *a: None,
                       _apply_configured_preview_filters=lambda p: p,
                       _append_profile_to_session_cache=lambda s, p: s['profiles'].append(p),
                       PointCloudProcessor=types.SimpleNamespace(merge_point_clouds=lambda _: 
                           types.SimpleNamespace(points=cloud.copy(), intensities=None)))
        path = Path(__file__).parents[1] / 'app/api/endpoints/acquisition.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        names = {'_update_session_once', '_update_session_locked', '_calc_speed_from_encoder_rpm'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), self.ns)

    def update(self, now, new_frame=True):
        self.clock = now
        if new_frame:
            self.frame += 1
        self.ns['_update_session_once'](self.session, self.receiver)

    def test_delayed_cycle_does_not_invent_profiles(self):
        self.update(1.)
        self.assertEqual(len(self.session['profiles']), 1)
        self.assertEqual(self.session['profiles'][0][0, 1], 200.)

    def test_repeated_frame_is_not_recorded_twice(self):
        self.update(1.)
        self.update(2., new_frame=False)
        self.assertEqual(len(self.session['profiles']), 1)

    def test_start_delay_uses_absolute_distance(self):
        self.motion['fixed_speed_mps'] = .01
        self.session['start_delay_mm_remaining'] = 100.
        for t in range(1, 10):
            self.update(float(t))
        self.assertEqual(len(self.session['profiles']), 0)
        self.update(10.)
        self.assertEqual(len(self.session['profiles']), 1)

    def test_stale_encoder_does_not_use_fallback(self):
        self.motion.update(mode='encoder', encoder_rps=10, encoder_wheel_value_mm=200)
        self.session.update(encoder_speed_mps=.2, encoder_sample_monotonic=0.)
        self.update(1.)
        self.assertEqual(self.session['distance_mm'], 0.)
        self.assertIn('encoder_stale', self.session['quality_issues'])

    def test_stationary_capture_does_not_grow_buffer(self):
        self.motion['fixed_speed_mps'] = 0.
        for t in range(100):
            self.update(float(t))
        self.assertEqual(self.session['pending_recording_frames'], [])
        self.assertEqual(self.session['profiles'], [])

    def test_reverse_rpm_preserves_sign(self):
        self.assertLess(self.ns['_calc_speed_from_encoder_rpm'](-60, {
            'encoder_wheel_mode': 'circumference', 'encoder_wheel_value_mm': 200}), 0)

    def test_skipped_frames_are_reported(self):
        self.update(1.)
        self.frame += 5
        self.update(2.)
        self.assertIn('frames_skipped', self.session['quality_issues'])

    def test_stopped_session_cannot_be_updated(self):
        self.session['recording'] = False
        self.update(1.)
        self.assertEqual(self.session['distance_mm'], 0.)
        self.assertEqual(self.session['profiles'], [])


if __name__ == '__main__':
    unittest.main()
