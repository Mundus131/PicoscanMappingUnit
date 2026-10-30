import unittest
import numpy as np
from app.analysis.log_measurement import compute_log_metrics
from app.schemas.device import AnalysisSettings


class LogVolumeMethodTests(unittest.TestCase):
    def metrics(self, radii, method='integration'):
        angles = np.linspace(0, 2 * np.pi, 100, endpoint=False)
        points = np.vstack([np.column_stack((r * np.cos(angles),
            np.full(100, i * 1000.), r * np.sin(angles))) for i, r in enumerate(radii)])
        return compute_log_metrics(points, 1000, window_profiles=1, min_points=50,
                                   volume_method=method)

    def test_cylinder_uses_mean_diameter_not_mean_area(self):
        result = self.metrics([100, 400, 200], 'end_diameter_cylinder')
        expected = np.pi * 150**2 * 2000
        self.assertAlmostEqual(result['volume_mm3'] / expected, 1., places=6)
        self.assertAlmostEqual(result['volume_m3'], expected / 1e9, places=7)
        self.assertEqual(result['volume_method'], 'end_diameter_cylinder')
        self.assertAlmostEqual(result['diameter_mm']['start'], 200, places=3)
        self.assertAlmostEqual(result['diameter_mm']['end'], 400, places=3)

    def test_default_keeps_trapezoidal_integration(self):
        result = self.metrics([100, 400, 200])
        expected = np.pi * ((100**2 + 400**2) / 2 + (400**2 + 200**2) / 2) * 1000
        self.assertAlmostEqual(result['volume_mm3'] / expected, 1., places=6)
        self.assertEqual(result['volume_method'], 'integration')

    def test_constant_diameter_matches_both_methods(self):
        self.assertAlmostEqual(self.metrics([100, 100])['volume_m3'],
            self.metrics([100, 100], 'end_diameter_cylinder')['volume_m3'])

    def test_single_slice_has_zero_length_and_volume(self):
        result = self.metrics([100], 'end_diameter_cylinder')
        self.assertEqual(result['total_length_mm'], 0)
        self.assertEqual(result['volume_m3'], 0)

    def test_unknown_method_rejected(self):
        with self.assertRaises(ValueError):
            self.metrics([100, 200], 'invalid')
        with self.assertRaises(ValueError):
            AnalysisSettings(active_app='log', log_volume_method='invalid')
