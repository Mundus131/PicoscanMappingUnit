import ast
import logging
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import numpy as np


class AnalysisFilterInputTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'app/api/endpoints/acquisition.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_run_analysis_local')
        self.metrics = Mock(return_value={'length_mm': 10})
        self.save = Mock()
        self.filter = Mock(side_effect=lambda p, f, c: p[p[:, 0] < 10])
        self.ns = dict(np=np, time=time, logger=logging.getLogger(__name__),
            device_manager=SimpleNamespace(analysis_settings={'active_app': 'log'}, output_settings={}),
            _apply_configured_preview_filters=self.filter, compute_log_metrics=self.metrics,
            build_augmented_cloud=lambda *a, **k: [], _save_measurement_async=self.save,
            _capture_quality=lambda s: {'measurement_valid': not bool(s.get('quality_issues'))})
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), self.ns)
        self.profiles = [np.array([[1, y, 2, 30], [100, y, 2, 40]], dtype=np.float32) for y in (0, 10)]
        self.session = dict(captured_profiles=self.profiles, captured_profile_positions=[0, 10],
            profiles_count=2, analysis_filter_settings={'frame': {'clip_points_to_frame': True}, 'filters': {}})

    def run_analysis(self):
        self.ns['_run_analysis_local'](self.session, SimpleNamespace(state=SimpleNamespace(tcp_notifier=None)))

    def test_metrics_receive_filtered_points_and_matching_profile_ids(self):
        self.run_analysis()
        self.metrics.assert_called_once()
        points = self.metrics.call_args.args[0]
        self.assertEqual(len(points), 2)
        self.assertTrue(all(p[0] == 1 for p in points))
        np.testing.assert_array_equal(self.metrics.call_args.kwargs['profile_ids'], [0, 1])
        self.assertEqual([len(p) for p in self.profiles], [2, 2])
        self.assertEqual(self.save.call_args.args[1]['profile_point_counts'], [1, 1])

    def test_empty_filtered_cloud_never_falls_back_to_raw_for_metrics(self):
        self.filter.side_effect = lambda p, f, c: p[:0]
        self.run_analysis()
        self.metrics.assert_not_called()
        self.assertIn('analysis_empty_after_filters', self.session['quality_issues'])
        self.assertFalse(self.session['measurement_valid'])
