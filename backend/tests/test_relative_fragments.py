import ast
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import unittest
import numpy as np
from app.services.capture_pipeline import MotionHistory


class RelativeFragmentsTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'app/api/endpoints/acquisition.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        names = {'_position_for_frame', '_consume_positioned_groups', '_retain_relative_fragment'}
        def append(session, points):
            session.setdefault('captured_profiles', []).append(points)
            session['captured_points_count'] = session.get('captured_points_count', 0) + len(points)
        self.ns = dict(np=np, deque=deque, _append_profile_to_session_cache=append,
            PointCloudProcessor=SimpleNamespace(merge_point_clouds=lambda inputs:
                SimpleNamespace(points=inputs[0][0], intensities=None)))
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name in names], type_ignores=[]), str(path), 'exec'), self.ns)
        self.history = MotionHistory()
        for t, v in [(0, 1), (.2, 1), (.4, None), (.6, 1), (.8, 1), (1., 1)]:
            self.history.add(t, v)
        self.session = dict(motion_history=self.history, capture_motion={'mode': 'encoder'},
            capture_started_monotonic=0, capture_calibrations={'a': {}},
            profiling_distance_mm=10, pending_profile_groups=deque(), captured_profiles=[])

    def consume(self, stamp):
        self.session['pending_profile_groups'].append({'a': dict(received_monotonic=stamp,
            points=np.array([[1, 0, 2, 50]], dtype=np.float32))})
        self.ns['_consume_positioned_groups'](self.session)

    def test_resumes_locally_without_joining_unknown_displacement(self):
        self.consume(.7)
        self.consume(.9)
        self.assertEqual(self.session['captured_profiles'], [])
        fragment = self.session['relative_fragments'][0]
        self.assertEqual(fragment['profiles_count'], 2)
        self.assertIsNone(fragment['global_offset_mm'])
        np.testing.assert_allclose(fragment['captured_profile_positions'], [0, 200])
        self.assertIn('encoder_history_gap', self.session['quality_issues'])

    def test_second_gap_creates_second_fragment(self):
        self.consume(.7)
        self.history.add(1.2, None)
        self.history.add(1.4, -1)
        self.history.add(1.6, -1)
        self.history.add(1.8, -1)
        self.consume(1.5)
        self.consume(1.7)
        self.assertEqual(len(self.session['relative_fragments']), 2)
        np.testing.assert_allclose(self.session['relative_fragments'][1]['captured_profile_positions'], [0, -200])

    def test_invalid_interval_is_not_given_invented_position(self):
        self.consume(.3)
        self.assertEqual(self.session.get('relative_fragments', []), [])

    def test_start_inside_gap_still_allows_later_fragment(self):
        self.session['capture_started_monotonic'] = .4
        self.consume(.7)
        self.assertEqual(self.session['relative_fragments'][0]['profiles_count'], 1)
