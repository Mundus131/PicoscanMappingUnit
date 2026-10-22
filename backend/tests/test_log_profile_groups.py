import sys
from pathlib import Path
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.analysis.log_measurement import compute_log_metrics


class ProfileGroupTests(unittest.TestCase):
    def test_scanner_skew_does_not_split_one_profile_into_two_bins(self):
        angle = np.linspace(0, 2 * np.pi, 100, endpoint=False)
        points = np.column_stack([100 * np.cos(angle), np.where(angle < np.pi, 4.9, 5.1), 100 * np.sin(angle)])
        result = compute_log_metrics(points, 10, window_profiles=1, min_points=80,
                                     profile_ids=np.zeros(100, dtype=int), profile_positions=[5.])
        self.assertEqual(result['total_slices'], 1)
        self.assertAlmostEqual(result['diameter_mm']['avg'], 200, places=3)

    def test_group_metadata_must_match_point_count(self):
        with self.assertRaises(ValueError):
            compute_log_metrics([[0, 0, 0]], 10, profile_ids=np.array([0, 1]))
