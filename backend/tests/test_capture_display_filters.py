"""Display filtering must never alter measurement profiles or resurrect empty results."""
import ast
import base64
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np


class CaptureDisplayFilterTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'app/api/endpoints/acquisition.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        names = {'_filtered_capture_display', '_downsample_points_np',
                 '_apply_configured_preview_filters'}
        self.manager = SimpleNamespace(frame_settings={}, preview_filter_settings={})
        self.processor = Mock()
        self.ns = dict(np=np, json=json, base64=base64, device_manager=self.manager,
                       PointCloudProcessor=self.processor,
                       _region_bounds_from_normalized_rect=lambda *a: (0, 10, 0, 10))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), self.ns)
        self.profile = np.array([[1, 20, 2, 50], [100, 20, 2, 60]], dtype=np.float32)
        self.session = dict(captured_profiles=[self.profile], captured_points_count=2,
                            last_points=[[999, 0, 999, 1]])

    def display(self, limit=0):
        return self.ns['_filtered_capture_display'](self.session, limit)

    def test_all_configured_filters_run_on_copies(self):
        self.manager.frame_settings = {'clip_points_to_frame': True}
        self.manager.preview_filter_settings = dict.fromkeys(
            ['use_region_filter', 'use_voxel_denoise', 'use_orthogonal_filter',
             'use_noise_filter', 'use_edge_filter'], True)
        methods = ['clip_points_to_frame', 'filter_region_xz', 'filter_voxel_density',
                   'filter_orthogonal_directions_xz', 'filter_statistical_noise',
                   'filter_edge_points']
        for name in methods:
            getattr(self.processor, name).side_effect = lambda p, *a, **kw: p
        original = self.profile.copy()
        self.display()
        self.assertEqual([c[0] for c in self.processor.mock_calls], methods)
        filtered_input = self.processor.clip_points_to_frame.call_args.args[0]
        self.assertFalse(np.shares_memory(filtered_input, self.profile))
        np.testing.assert_array_equal(self.profile, original)

    def test_empty_filtered_capture_does_not_fall_back_to_live_points(self):
        self.manager.frame_settings = {'clip_points_to_frame': True}
        self.processor.clip_points_to_frame.side_effect = lambda p, f: p[:0]
        for limit in (0, 10):
            result = self.display(limit)
            self.assertEqual(result['points_count'], 0)
            self.assertEqual(result['total_points'], 0)
            self.assertEqual(result['points'], [])
            self.assertEqual(result['captured_points_count'], 2)

    def test_settings_change_invalidates_cache_and_disabled_filter_restores_points(self):
        self.manager.frame_settings = {'clip_points_to_frame': True}
        self.processor.clip_points_to_frame.side_effect = lambda p, f: p[p[:, 0] < 10]
        self.assertEqual(self.display()['points_count'], 1)
        self.display()
        self.processor.clip_points_to_frame.assert_called_once()
        self.manager.frame_settings = {'clip_points_to_frame': False}
        self.assertEqual(self.display()['points_count'], 2)

    def test_filter_before_downsampling_and_binary_count_matches(self):
        self.manager.frame_settings = {'clip_points_to_frame': True}
        self.processor.clip_points_to_frame.side_effect = lambda p, f: p[p[:, 0] > 10]
        result = self.display(1)
        decoded = np.frombuffer(base64.b64decode(result['points_blob_b64']), np.float32)
        np.testing.assert_array_equal(decoded.reshape(-1, result['point_stride']), self.profile[1:])
        self.assertEqual(result['points_count'], 1)
        self.assertEqual(self.display()['points'], self.profile[1:].tolist())

    def test_new_profile_is_filtered_without_refiltering_old_profile(self):
        self.manager.frame_settings = {'clip_points_to_frame': True}
        self.processor.clip_points_to_frame.side_effect = lambda p, f: p
        self.display()
        self.session['captured_profiles'].append(self.profile.copy())
        self.assertEqual(self.display()['points_count'], 4)
        self.assertEqual(self.processor.clip_points_to_frame.call_count, 2)

    def test_empty_session_has_valid_binary_stride(self):
        self.session = {}
        result = self.display(10)
        self.assertEqual(result['point_stride'], 4)
        self.assertEqual(result['points_count'], 0)
