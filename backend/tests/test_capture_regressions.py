"""Exercise acquisition math without starting hardware clients on import."""
import ast
import logging
from pathlib import Path
import threading
import types
import unittest
import sys
from collections import deque
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.services.capture_pipeline import MotionHistory, FrameSynchronizer

import numpy as np


class CaptureRegressionTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.frame = 1
        self.motion = {"mode": "fixed", "fixed_speed_mps": 0.2, "profiling_distance_mm": 5}
        self.session = dict(recording=True, last_motion_monotonic=0., distance_mm=0.,
                            last_profile_distance_mm=0., devices=["scanner"], profiles=[])
        cloud = np.array([[1., 0., 2.], [2., 0., 3.]])
        self.receiver = types.SimpleNamespace(drain_point_clouds=lambda _: [{
            "frame": self.frame, "timestamp": self.clock, "received_monotonic": self.clock,
            "complete": True, "points": cloud}], get_health_snapshot=lambda: {})
        self.session.update(capture_motion=self.motion, capture_started_monotonic=0.,
            capture_calibrations={"scanner": {}}, receiver_health_baseline={}, device_last_frame_monotonic={},
            frame_synchronizer=FrameSynchronizer(["scanner"]), pending_profile_groups=deque(),
            motion_history=MotionHistory(), profiling_distance_mm=5.)
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
        names = {'_update_session_once', '_update_session_locked', '_calc_speed_from_encoder_rpm', '_position_for_frame', '_consume_positioned_groups', '_extract_encoder_rpm'}
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
        self.assertEqual(list(self.session['pending_profile_groups']), [])
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

    def test_all_queued_frames_have_their_own_position(self):
        cloud = np.array([[1., 0., 2.]])
        self.receiver.drain_point_clouds = lambda _: [dict(frame=i, received_monotonic=t,
            complete=True, points=cloud) for i, t in enumerate((.1, .2, .3), 1)]
        self.update(.4)
        self.assertEqual(len(self.session['profiles']), 3)
        np.testing.assert_allclose([p[0, 1] for p in self.session['profiles']], [20, 40, 60])

    def test_preview_filters_cannot_remove_measurement_points(self):
        self.ns['_apply_configured_preview_filters'] = lambda p: p[:0]
        self.update(1.)
        self.assertEqual(self.session['last_points'], [])
        self.assertEqual(len(self.session['profiles'][0]), 2)

    def test_frame_waits_for_following_encoder_sample(self):
        self.motion['mode'] = 'encoder'
        history = self.session['motion_history']
        history.add(0, .2)
        self.session.update(encoder_speed_mps=.2, encoder_sample_monotonic=0.)
        self.update(.1)
        self.assertEqual(self.session['profiles'], [])
        history.add(.2, .4)
        self.session['encoder_sample_monotonic'] = .2
        self.receiver.drain_point_clouds = lambda _: []
        self.update(.2)
        self.assertAlmostEqual(self.session['profiles'][0][0, 1], 25.)

    def test_two_scanners_keep_their_own_encoder_position(self):
        self.session['devices'] = ['a', 'b']
        self.session['capture_calibrations'] = {'a': {}, 'b': {}}
        self.session['frame_synchronizer'] = FrameSynchronizer(['a', 'b'])
        self.receiver.drain_point_clouds = lambda d: [dict(frame=1, complete=True,
            received_monotonic=.1 if d == 'a' else .11, points=np.array([[1., 0., 2.]]))]
        self.update(.2)
        self.assertEqual(len(self.session['profiles']), 1)
        np.testing.assert_allclose(self.session['profiles'][0][:, 1], [20, 20, 22, 22])

    def test_origin_survives_bounded_history_eviction(self):
        self.motion['mode'] = 'encoder'
        history = MotionHistory(capacity=2)
        self.session['motion_history'] = history
        history.add(0, .2)
        history.add(.1, .2)
        self.assertAlmostEqual(self.ns['_position_for_frame'](self.session, .1)[0], 20)
        history.add(.2, .2)
        self.assertAlmostEqual(self.ns['_position_for_frame'](self.session, .2)[0], 40)

    def test_stop_timestamp_excludes_frames_after_stop(self):
        self.session['capture_stopped_monotonic'] = .1
        self.update(.2)
        self.assertEqual(self.session['profiles'], [])

    def test_byte_array_is_not_speed_and_nan_rejected(self):
        extract = self.ns['_extract_encoder_rpm']
        self.assertIsNone(extract({'data': {'getData': {'iolink': {'value': [0, 1, 2]}}}}))
        def payload(value):
            return {'data': {'getData': {'iolink': {'value': {'Velocity': {'value': value}}}}}}
        self.assertEqual(extract(payload(60)), 60)
        self.assertIsNone(extract(payload('nan')))


if __name__ == '__main__':
    unittest.main()
