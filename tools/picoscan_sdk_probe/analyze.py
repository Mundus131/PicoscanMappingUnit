"""Summarize SDK observations; never infer completeness from the modal segment count."""
import argparse
import csv
import json
from collections import Counter, defaultdict


def analyze(rows, expected=None):
    rows = list(rows)
    frames = defaultdict(list)
    for row in rows:
        frames[int(row['frame'])].append(row)
    ordered = list(frames)
    interior = ordered[1:-1]  # Capture can start/end halfway through a scan.
    patterns = Counter(tuple(sorted({int(r['segment']) for r in frames[f]})) for f in interior)
    mismatches = None if expected is None else sum(
        {int(r['segment']) for r in frames[f]} != expected for f in interior)
    elapsed = (float(rows[-1]['host_elapsed_s']) - float(rows[0]['host_elapsed_s'])
               if len(rows) > 1 and 'host_elapsed_s' in rows[0] else 0)
    return {
        'observed_frames': len(frames), 'interior_frames': len(interior),
        'observed_frame_rate_hz': (len(frames)-1)/elapsed if elapsed > 0 else None,
        'interior_segment_patterns': {','.join(map(str, k)): v for k, v in patterns.items()},
        'expected_segment_ids': None if expected is None else sorted(expected),
        'frames_with_wrong_segment_set': mismatches,
        'duplicate_segments': sum(len(rs)-len({r['segment'] for r in rs}) for rs in frames.values()),
        'zero_positive_distance_segments': sum(int(r['positive_samples']) == 0 for rs in frames.values() for r in rs),
        'sdk_points': sum(int(r['sdk_points']) for rs in frames.values() for r in rs),
        'telegram_sequence_discontinuities': sum(
            int(b['telegram']) != int(a['telegram']) + 1
            for a, b in zip(rows, rows[1:]) if 'telegram' in a and 'telegram' in b),
        'note': 'Frame count is observed, not a guarantee of complete scans. No encoder or acquisition validation.'
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('csv')
    parser.add_argument('--segment-ids', help='Exact expected IDs from verified sensor configuration, e.g. 0,1,2')
    args = parser.parse_args()
    with open(args.csv, newline='', encoding='utf-8') as stream:
        expected = None if args.segment_ids is None else set(map(int, args.segment_ids.split(',')))
        print(json.dumps(analyze(csv.DictReader(stream), expected), indent=2))
