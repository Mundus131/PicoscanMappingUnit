import ast
from pathlib import Path
import unittest
import numpy as np


class PreviewSamplingTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'app/api/endpoints/acquisition.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_downsample_points_np')
        ns = {'np': np}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
        self.sample = ns['_downsample_points_np']

    def test_new_profiles_are_not_truncated(self):
        cloud = np.zeros((45521, 4), dtype=np.float32)
        cloud[:, 1] = np.arange(len(cloud))
        result = self.sample(cloud, 40000)
        self.assertEqual(len(result), 40000)
        self.assertEqual(result[0, 1], 0)
        self.assertEqual(result[-1, 1], 45520)

    def test_repeated_append_retains_full_extent(self):
        preview = np.empty((0, 4), dtype=np.float32)
        for i in range(31):
            profile = np.full((5521, 4), i, dtype=np.float32)
            preview = self.sample(np.vstack((preview, profile)), 40000)
        self.assertEqual(len(np.unique(preview[:, 1])), 31)
        self.assertEqual(preview[-1, 1], 30)

    def test_input_is_not_modified(self):
        cloud = np.ones((10, 4), dtype=np.float32)
        result = self.sample(cloud, 20)
        result[:] = 0
        self.assertTrue(np.all(cloud == 1))
