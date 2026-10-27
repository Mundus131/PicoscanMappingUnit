import unittest
from analyze import analyze


def row(frame, segment, positive=10):
    return dict(frame=str(frame), segment=str(segment), positive_samples=str(positive), sdk_points='10')


class AnalysisTests(unittest.TestCase):
    def test_no_guessed_completeness(self):
        self.assertIsNone(analyze([row(1, 0)])['frames_with_wrong_segment_set'])

    def test_boundaries_and_missing_segment(self):
        result = analyze([row(1, 1), row(2, 0), row(3, 0), row(3, 1), row(4, 0)], {0, 1})
        self.assertEqual(result['frames_with_wrong_segment_set'], 1)
        self.assertEqual(result['interior_frames'], 2)

    def test_duplicate_does_not_replace_missing_segment(self):
        result = analyze([row(1, 0), row(2, 0), row(2, 0, 0), row(3, 1)], {0, 1})
        self.assertEqual(result['duplicate_segments'], 1)
        self.assertEqual(result['frames_with_wrong_segment_set'], 1)
        self.assertEqual(result['zero_positive_distance_segments'], 1)


if __name__ == '__main__':
    unittest.main()
